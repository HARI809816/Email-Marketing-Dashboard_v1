from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
from app.schemas import (
    UserCreate, 
    UserResponse, 
    UserDetailResponse,
    LoginRequest, 
    Token, 
    UserRole, 
    PasswordUpdate, 
    AdminPasswordUpdate,
    ClientCreate,
    ClientResponse,
    ManuscriptCreate,
    ManuscriptResponse,
    OrderCreate,
    OrderResponse,
    PaymentCreate,
    PaymentResponse,
    DashboardOrderResponse,
    LoginResponse,
    OTPVerifyRequest,
    PermissionUpdate,
    DashboardUpdate,
    ApiResponse,
    ClientAssignRequest,
    UnifiedCreateRequest
)
import random
import smtplib
from email.message import EmailMessage
from app.config import (
    SMTP_SERVER, 
    SMTP_PORT, 
    SMTP_USERNAME, 
    SMTP_PASSWORD, 
    EMAIL_FROM
)
from app.auth import (
    encrypt_password,
    decrypt_password,
    verify_password,
    create_access_token,
    get_current_user,
    require_admin,
    require_manager_or_higher
)
from app.database import (
    users_collection, 
    tokens_collection,
    clients_collection,
    manuscripts_collection,
    orders_collection,
    payments_collection,
    otps_collection
)
from bson import ObjectId

app = FastAPI(title="Email Dashboard API")

# --- CORS CONFIGURATION ---
origins = [
    "https://marketing-dashboard123.vercel.app",
    "http://localhost:5173",
    "https://unflushed-uninterpretively-corey.ngrok-free.dev",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)


# --- CUSTOM EXCEPTION HANDLERS ---

@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status_code": exc.status_code,
            "status": "error",
            "message": exc.detail,
            "data": None
        }
    )

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "status_code": 500,
            "status": "error",
            "message": "Internal Server Error",
            "data": str(exc) if "DEV" in str(request.headers) else None
        }
    )

# --- HELPER ---
def send_otp_email(to_email: str, otp: str):
    """
    Sends an OTP email via SMTP.
    """
    msg = EmailMessage()
    msg.set_content(f"Your OTP for login is: {otp}\n\nThis OTP is valid for 5 minutes.")
    msg["Subject"] = "Login OTP - Email Dashboard"
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email

    # DEBUG: Print OTP for testing purposes
    print(f"\n🔐 [TEST OTP] For email {to_email}: {otp}")
    print("⚠️  This OTP is printed for testing only!\n")

    print(f"\n[OTP DEBUG] Attempting to send email to {to_email} via {SMTP_SERVER}:{SMTP_PORT}\n")
    try:
        if SMTP_PORT == 465:
            # Port 465 requires SMTP_SSL from the start
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            # Port 587 (and others) typically use STARTTLS
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def format_mongo_id(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def resolve_client_handler(client: dict) -> dict:
    """
    Resolves client_handler (email) to client_handler_name (full_name) for display.
    client_handler stores the employee's email for uniqueness.
    client_handler_name is the human-readable full name shown on the frontend.
    """
    handler_email = client.get("client_handler")
    if handler_email:
        handler_user = users_collection.find_one({"email": handler_email})
        client["client_handler_name"] = handler_user.get("full_name") if handler_user else handler_email
    else:
        client["client_handler_name"] = None
    return client


@app.get("/", response_model=ApiResponse[dict])
def read_root():
    return {
        "status_code": 200,
        "status": "success",
        "message": "Welcome to Email Dashboard API",
        "data": None
    }

# --- INITIALIZATION ---

@app.post("/init-super-admin", response_model=ApiResponse[dict], status_code=status.HTTP_201_CREATED)
def init_super_admin(user: UserCreate):
    """
    Endpoint to initialize the first super admin users. 
    Works if fewer than 5 admins exist in the database.
    """
    admin_count = users_collection.count_documents({"role": UserRole.ADMIN})
    if admin_count >= 5:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Maximum of 5 Admins already exist"
        )
    
    user_dict = user.model_dump()
    user_dict["password"] = encrypt_password(user.password)
    user_dict["role"] = UserRole.ADMIN
    users_collection.insert_one(user_dict)
    
    return {
        "status_code": 201,
        "status": "success",
        "message": "Super Admin created successfully",
        "data": None
    }

# --- LOGIN ---

@app.post("/login", response_model=ApiResponse[LoginResponse])
def login(request: LoginRequest):
    """
    Shared login endpoint. Admins and Managers require OTP.
    Employees login directly.
    """
    user = users_collection.find_one({"email": request.email})
    if not user or not verify_password(request.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if role requires OTP (Admin and Manager)
    if user["role"] in [UserRole.ADMIN, UserRole.MANAGER]:
        otp = str(random.randint(100000, 999999))
        
        # Store OTP
        otps_collection.update_one(
            {"email": user["email"]},
            {"$set": {
                "otp": otp,
                "created_at": datetime.utcnow()
            }},
            upsert=True
        )
        
        # Send OTP
        sent = send_otp_email(user["email"], otp)
        
        return {
            "status_code": 200,
            "status": "success",
            "message": "OTP required move to verify-otp",
            "data": LoginResponse(otp_required=True, email=user["email"])
        }

    # Regular login for Employee
    access_token = create_access_token(data={"sub": user["email"]})
    
    # Store token
    tokens_collection.insert_one({
        "user_email": user["email"],
        "token": access_token,
        "created_at": datetime.utcnow()
    })
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "Login successful",
        "data": LoginResponse(access_token=access_token, token_type="bearer")
    }

@app.post("/verify-otp", response_model=ApiResponse[Token])
def verify_otp(request: OTPVerifyRequest):
    """
    Verify OTP for Admin/Manager login.
    """
    # Check OTP record
    otp_record = otps_collection.find_one({"email": request.email})
    
    if not otp_record or otp_record["otp"] != request.otp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired OTP"
        )
    
    # OTP is valid, check expiration (e.g., 5 minutes)
    if datetime.utcnow() - otp_record["created_at"] > timedelta(minutes=5):
        otps_collection.delete_one({"email": request.email})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="OTP has expired"
        )
    
    # OTP verified, issue token
    user = users_collection.find_one({"email": request.email})
    access_token = create_access_token(data={"sub": user["email"]})
    
    # Store token
    tokens_collection.insert_one({
        "user_email": user["email"],
        "token": access_token,
        "created_at": datetime.utcnow()
    })
    
    # Clear OTP
    otps_collection.delete_one({"email": request.email})
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "OTP verified successfully",
        "data": {"access_token": access_token, "token_type": "bearer"}
    }

# --- USER & ADMIN CREATION ---

@app.post("/users", response_model=ApiResponse[UserResponse], status_code=status.HTTP_201_CREATED)
def create_user(user: UserCreate, current_user: dict = Depends(require_manager_or_higher)):
    """
    Create a new User (Admin, Manager, or Employee).
    Restricted to Super Admin and Manager. 
    One additional Admin is allowed (total 2).
    """
    # Check if user already exists
    if users_collection.find_one({"email": user.email}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Email already registered"
        )
    
    # Logic for Admin role restriction
    if user.role == UserRole.ADMIN:
        # Only existing Admin can create another Admin
        if current_user["role"] != UserRole.ADMIN:
             raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only Super Admin can create another Admin"
            )
             
        admin_count = users_collection.count_documents({"role": UserRole.ADMIN})
        if admin_count >= 5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum of 5 Admins allowed"
            )
            
    user_dict = user.model_dump()
    user_dict["password"] = encrypt_password(user.password)
    result = users_collection.insert_one(user_dict)
    
    user_dict["_id"] = str(result.inserted_id)
    user_dict["password"] = user.password
    return {
        "status_code": 201,
        "status": "success",
        "message": "User created successfully",
        "data": user_dict
    }

@app.post("/managers", response_model=ApiResponse[UserResponse], status_code=status.HTTP_201_CREATED)
def create_manager(user: UserCreate, current_user: dict = Depends(require_manager_or_higher)):
    """
    Create a new Manager.
    Restricted to Admin and Manager only.
    """
    # Check if user already exists
    if users_collection.find_one({"email": user.email}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Email already registered"
        )
    
    # Enforce role to be Manager
    user_dict = user.model_dump()
    user_dict["role"] = UserRole.MANAGER
    user_dict["password"] = encrypt_password(user.password)
    result = users_collection.insert_one(user_dict)
    
    user_dict["_id"] = str(result.inserted_id)
    user_dict["password"] = user.password
    return {
        "status_code": 201,
        "status": "success",
        "message": "Manager created successfully",
        "data": user_dict
    }

# --- PASSWORD MANAGEMENT ---

@app.put("/users/me/password", response_model=ApiResponse[dict])
def update_own_password(data: PasswordUpdate, current_user: dict = Depends(get_current_user)):
    """
    Update own password. Available to all roles.
    """
    hashed_password = encrypt_password(data.new_password)
    users_collection.update_one(
        {"email": current_user["email"]},
        {"$set": {"password": hashed_password}}
    )
    return {
        "status_code": 200,
        "status": "success",
        "message": "Password updated successfully",
        "data": None
    }

@app.put("/users/password", response_model=ApiResponse[dict])
def update_user_password(data: AdminPasswordUpdate, current_user: dict = Depends(require_manager_or_higher)):
    """
    Update a User's password. Restricted to Admin and Super Admin.
    Admins can only change USER role passwords.
    Super Admins can change ADMIN and USER role passwords.
    """
    target_user = users_collection.find_one({"email": data.email})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Admin can only change EMPLOYEE passwords
    if current_user["role"] == UserRole.MANAGER:
        if target_user["role"] != UserRole.EMPLOYEE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Managers can only change Employee passwords"
            )
    
    # Super Admin can change Admin or Manager passwords
    if current_user["role"] == UserRole.ADMIN:
         if target_user["role"] == UserRole.ADMIN and target_user["email"] != current_user["email"]:
              raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admins cannot change other Admin passwords"
            )

    hashed_password = encrypt_password(data.new_password)
    users_collection.update_one(
        {"email": data.email},
        {"$set": {"password": hashed_password}}
    )
    return {
        "status_code": 200,
        "status": "success",
        "message": f"Password for {data.email} updated successfully",
        "data": None
    }

# --- VISIBILITY ---

@app.get("/users", response_model=ApiResponse[list[UserResponse]])
def get_all_users(current_user: dict = Depends(require_manager_or_higher)):
    """
    Get all regular Users. Accessible to Admin and Super Admin.
    """
    users = list(users_collection.find({"role": UserRole.EMPLOYEE}))
    for u in users:
        u["_id"] = str(u["_id"])
        u["password"] = decrypt_password(u.get("password", ""))
    return {
        "status_code": 200,
        "status": "success",
        "message": "Users fetched successfully",
        "data": users
    }

@app.get("/admins", response_model=ApiResponse[list[UserResponse]])
def get_all_admins(current_user: dict = Depends(require_admin)):
    """
    Get all Admins and Super Admins. Accessible to Super Admin only.
    """
    admins = list(users_collection.find({"role": {"$in": [UserRole.MANAGER, UserRole.ADMIN]}}))
    for a in admins:
        a["_id"] = str(a["_id"])
        a["password"] = decrypt_password(a.get("password", ""))
    return {
        "status_code": 200,
        "status": "success",
        "message": "Admins fetched successfully",
        "data": admins
    }

@app.put("/users/permissions", response_model=ApiResponse[dict])
def update_user_permissions(data: PermissionUpdate, current_user: dict = Depends(require_manager_or_higher)):
    """
    Update an Employee's column-level permissions. 
    Restricted to Admin and Manager.
    """
    target_user = users_collection.find_one({"email": data.email})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if target is indeed an employee
    if target_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Permissions can only be set for Employees"
        )

    users_collection.update_one(
        {"email": data.email},
        {"$set": {"permissions": data.permissions}}
    )
    return {
        "status_code": 200,
        "status": "success",
        "message": f"Permissions updated for {data.email}",
        "data": None
    }

@app.get("/users/me/details", response_model=ApiResponse[UserDetailResponse])
def get_own_details(current_user: dict = Depends(get_current_user)):
    """
    Get current user profile details including nested handled clients and dashboard stats.
    Optimized with MongoDB Aggregation Pipeline.
    """
    # 1. Determine client filter
    client_match = {}
    if current_user["role"] not in [UserRole.ADMIN, UserRole.MANAGER]:
        client_match = {"client_handler": current_user.get("email")}

    # 2. Aggregation Pipeline to fetch clients and their stats in ONE go
    pipeline = [
        {"$match": client_match},
        {
            "$lookup": {
                "from": "orders",
                "localField": "client_id",
                "foreignField": "client_id",
                "as": "orders"
            }
        },
        {
            "$lookup": {
                "from": "payments",
                "localField": "client_id",
                "foreignField": "client_id",
                "as": "payments"
            }
        },
        {
            "$addFields": {
                "total_amount": {"$sum": "$orders.total_amount"},
                "writing_amount": {"$sum": "$orders.writing_amount"},
                "modification_amount": {"$sum": "$orders.modification_amount"},
                "po_amount": {"$sum": "$orders.po_amount"},
                "paid_amount": {"$sum": "$payments.amount"},
                "order_count": {"$size": "$orders"},
                "payment_status": {"$ifNull": [{"$arrayElemAt": ["$orders.payment_status", 0]}, "No Order"]},
                "pending_order_count": {
                    "$size": {
                        "$filter": {
                            "input": "$orders",
                            "as": "o",
                            "cond": {"$eq": ["$$o.payment_status", "Pending"]}
                        }
                    }
                }
            }
        }
    ]
    
    clients_with_stats = list(clients_collection.aggregate(pipeline))
    
    handled_clients = []
    country_split = {}
    total_system_amount = 0.0
    total_system_paid = 0.0
    total_system_orders = 0
    total_system_pending = 0
    
    for c in clients_with_stats:
        c["_id"] = str(c["_id"])
        c["remaining_amount"] = c["total_amount"] - c["paid_amount"]
        resolve_client_handler(c)
        
        # Calculate country split for Pie Chart
        country = c.get("country") or "Unknown"
        country_split[country] = country_split.get(country, 0.0) + c.get("total_amount", 0.0)
        
        # Pull global stats from client totals
        total_system_amount += c["total_amount"]
        total_system_paid += c["paid_amount"]
        total_system_orders += c["order_count"]
        total_system_pending += c["pending_order_count"]

        print(c["client_id"])   
        print(c["total_amount"])
        
        # Cleanup extra fields not needed in response
        c.pop("orders", None)
        c.pop("payments", None)
        c.pop("order_count", None)
        c.pop("pending_order_count", None)
        handled_clients.append(c)

    # 3. Calculate Dashboard Stats
    total_clients_count = len(clients_with_stats)
    overall_amt_pct = (total_system_paid / total_system_amount * 100) if total_system_amount > 0 else 0.0
    pending_pct = (total_system_pending / total_system_orders * 100) if total_system_orders > 0 else 0.0
    
    dashboard_stats = {
        "overall_amount": total_system_amount,
        "overall_amount_percentage": round(overall_amt_pct, 1),
        "total_clients": total_clients_count,
        "total_clients_percentage": 100.0, 
        "pending_count": total_system_pending,
        "pending_count_percentage": round(pending_pct, 1),
        "reject_count": 0,
        "reject_count_percentage": 0.0
    }
    
    user_data = format_mongo_id(current_user.copy())
    user_data["password"] = decrypt_password(user_data.get("password", ""))
    user_data["handled_clients"] = handled_clients
    user_data["dashboard_stats"] = dashboard_stats
    user_data["country_split"] = country_split
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "User details fetched successfully",
        "data": user_data
    }

@app.get("/users/{email}/details", response_model=ApiResponse[UserDetailResponse])
def get_user_details(email: str, current_user: dict = Depends(require_manager_or_higher)):
    """
    Get profile details of any user including handled clients.
    Restricted to Admin and Manager.
    """
    target_user = users_collection.find_one({"email": email})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # Find clients handled by this target user (query by unique email)
    clients = list(clients_collection.find({"client_handler": target_user.get("email")}))
    
    user_data = format_mongo_id(target_user)
    user_data["password"] = decrypt_password(user_data.get("password", ""))
    for c in clients:
        format_mongo_id(c)
        resolve_client_handler(c)
    user_data["handled_clients"] = clients
    
    return {
        "status_code": 200,
        "status": "success",
        "message": f"Details for {email} fetched successfully",
        "data": user_data
    }

# --- CLIENTS ---

@app.post("/clients", response_model=ApiResponse[ClientResponse], status_code=status.HTTP_201_CREATED)
def create_client(client: ClientCreate, current_user: dict = Depends(get_current_user)):
    # Check if client_id already exists
    if clients_collection.find_one({"client_id": client.client_id}):
        raise HTTPException(status_code=400, detail="Client ID already exists")

    client_dict = client.model_dump()
    
    # Dynamic Client Handler logic — store EMAIL for uniqueness
    if not client_dict.get("client_handler"):
        if current_user["role"] == UserRole.EMPLOYEE:
            client_dict["client_handler"] = current_user.get("email")
        else:
            client_dict["client_handler"] = None
    # Remove display-only field before saving to DB
    client_dict.pop("client_handler_name", None)
            
    client_dict["created_at"] = datetime.utcnow()
    result = clients_collection.insert_one(client_dict)
    client_dict["_id"] = str(result.inserted_id)
    return {
        "status_code": 201,
        "status": "success",
        "message": "Client created successfully",
        "data": client_dict
    }

@app.get("/clients", response_model=ApiResponse[list[ClientResponse]])
def get_clients(current_user: dict = Depends(get_current_user)):
    query = {}
    if current_user["role"] == UserRole.EMPLOYEE:
        query = {"client_handler": current_user.get("email")}
    clients = list(clients_collection.find(query))
    resolved = [resolve_client_handler(format_mongo_id(c)) for c in clients]
    
    # Fetch all employees to extract unique profile names and employee names
    employees = list(users_collection.find({"role": UserRole.EMPLOYEE}))
    employee_names = set()
    profile_names = set()
    for emp in employees:
        if emp.get("full_name"):
            employee_names.add(emp["full_name"])
        if emp.get("profile_names") and isinstance(emp["profile_names"], list):
            for p in emp["profile_names"]:
                profile_names.add(p)
                
    detail = {
        "employee_names": list(employee_names),
        "profile_names": list(profile_names)
    }

    return {
        "status_code": 200,
        "status": "success",
        "message": "Clients fetched successfully",
        "data": resolved,
        "detail": detail
    }

@app.get("/clients/{client_id}", response_model=ApiResponse[ClientResponse])
def get_client(client_id: str, current_user: dict = Depends(require_manager_or_higher)):
    client = clients_collection.find_one({"client_id": client_id})
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return {
        "status_code": 200,
        "status": "success",
        "message": "Client fetched successfully",
        "data": resolve_client_handler(format_mongo_id(client))
    }

@app.post("/clients/assign", response_model=ApiResponse[ClientResponse])
def assign_client(request: ClientAssignRequest, current_user: dict = Depends(require_manager_or_higher)):
    """
    Assign an Employee to a Client.
    Restricted to Admin and Manager.
    """
    # 1. Verify Employee
    employee = users_collection.find_one({"email": request.employee_email, "role": UserRole.EMPLOYEE})
    if not employee:
        raise HTTPException(
            status_code=404, 
            detail=f"Employee with email {request.employee_email} not found"
        )
    
    # 2. Verify Client
    client = clients_collection.find_one({"client_id": request.client_id})
    if not client:
        raise HTTPException(
            status_code=404, 
            detail=f"Client with ID {request.client_id} not found"
        )
    
    # 3. Update Client Handler — store email for uniqueness
    clients_collection.update_one(
        {"client_id": request.client_id},
        {"$set": {"client_handler": employee.get("email")}}
    )
    
    # Fetch updated client
    updated_client = clients_collection.find_one({"client_id": request.client_id})
    
    return {
        "status_code": 200,
        "status": "success",
        "message": f"Client {request.client_id} assigned to {employee.get('full_name')}",
        "data": resolve_client_handler(format_mongo_id(updated_client))
    }

# --- MANUSCRIPTS ---

@app.post("/manuscripts", response_model=ApiResponse[ManuscriptResponse], status_code=status.HTTP_201_CREATED)
def create_manuscript(manuscript: ManuscriptCreate, current_user: dict = Depends(require_manager_or_higher)):
    # Verify client exists
    if not clients_collection.find_one({"client_id": manuscript.client_id}):
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    ms_dict = manuscript.model_dump()
    ms_dict["created_at"] = datetime.utcnow()
    result = manuscripts_collection.insert_one(ms_dict)
    ms_dict["_id"] = str(result.inserted_id)
    return {
        "status_code": 201,
        "status": "success",
        "message": "Manuscript created successfully",
        "data": ms_dict
    }

@app.get("/manuscripts", response_model=ApiResponse[list[ManuscriptResponse]])
def get_manuscripts(current_user: dict = Depends(get_current_user)):
    query = {}
    if current_user["role"] == UserRole.EMPLOYEE:
        # Get clients handled by this employee (filter by unique email)
        my_clients = list(clients_collection.find({"client_handler": current_user.get("email")}))
        my_client_ids = [c["client_id"] for c in my_clients]
        query = {"client_id": {"$in": my_client_ids}}
        
    ms = list(manuscripts_collection.find(query))
    return {
        "status_code": 200,
        "status": "success",
        "message": "Manuscripts fetched successfully",
        "data": [format_mongo_id(m) for m in ms]
    }

# --- ORDERS ---

@app.post("/orders", response_model=ApiResponse[OrderResponse], status_code=status.HTTP_201_CREATED)
def create_order(order: OrderCreate, current_user: dict = Depends(require_manager_or_higher)):
    # Verify client exists
    if not clients_collection.find_one({"client_id": order.client_id}):
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    # Manuscript is optional — only validate if provided
    if order.manuscript_id:
        if not manuscripts_collection.find_one({"manuscript_id": order.manuscript_id}):
            raise HTTPException(status_code=400, detail="Invalid manuscript_id")
    
    # Ensure reference_id is unique across all orders
    if orders_collection.find_one({"reference_id": order.reference_id}):
        raise HTTPException(status_code=400, detail="This reference ID already exists")
    
    order_dict = order.model_dump()
    order_dict["created_at"] = datetime.utcnow()
    order_dict["updated_at"] = datetime.utcnow()
    result = orders_collection.insert_one(order_dict)
    order_dict["_id"] = str(result.inserted_id)
    return {
        "status_code": 201,
        "status": "success",
        "message": "Order created successfully",
        "data": order_dict
    }

@app.get("/orders", response_model=ApiResponse[list[OrderResponse]])
def get_orders(current_user: dict = Depends(get_current_user)):
    query = {}
    if current_user["role"] == UserRole.EMPLOYEE:
        # Get clients handled by this employee (filter by unique email)
        my_clients = list(clients_collection.find({"client_handler": current_user.get("email")}))
        my_client_ids = [c["client_id"] for c in my_clients]
        query = {"client_id": {"$in": my_client_ids}}
        
    orders = list(orders_collection.find(query))
    return {
        "status_code": 200,
        "status": "success",
        "message": "Orders fetched successfully",
        "data": [format_mongo_id(o) for o in orders]
    }

# --- PAYMENTS ---

@app.post("/payments", response_model=ApiResponse[PaymentResponse], status_code=status.HTTP_201_CREATED)
def create_payment(payment: PaymentCreate, current_user: dict = Depends(require_manager_or_higher)):
    if not clients_collection.find_one({"client_id": payment.client_id}):
        raise HTTPException(status_code=400, detail="Invalid client_id")
    
    pay_dict = payment.model_dump()
    pay_dict["created_at"] = datetime.utcnow()
    result = payments_collection.insert_one(pay_dict)
    pay_dict["_id"] = str(result.inserted_id)
    return {
        "status_code": 201,
        "status": "success",
        "message": "Payment created successfully",
        "data": pay_dict
    }

@app.get("/payments", response_model=ApiResponse[list[PaymentResponse]])
def get_payments(current_user: dict = Depends(get_current_user)):
    query = {}
    if current_user["role"] == UserRole.EMPLOYEE:
        # Get clients handled by this employee (filter by unique email)
        my_clients = list(clients_collection.find({"client_handler": current_user.get("email")}))
        my_client_ids = [c["client_id"] for c in my_clients]
        query = {"client_id": {"$in": my_client_ids}}
        
    payments = list(payments_collection.find(query))
    return {
        "status_code": 200,
        "status": "success",
        "message": "Payments fetched successfully",
        "data": [format_mongo_id(p) for p in payments]
    }

# --- DASHBOARD ---

@app.get("/dashboard/orders", response_model=ApiResponse[list[DashboardOrderResponse]])
def get_dashboard_orders(current_user: dict = Depends(get_current_user)):
    """
    Unified endpoint for the frontend dashboard.
    Optimized with MongoDB Aggregation Pipeline ($lookup + $unwind).
    Shows clients even if no orders exist.
    """
    # 1. Get filtered clients query
    client_match = {}
    if current_user["role"] == UserRole.EMPLOYEE:
        client_match = {"client_handler": current_user.get("email")}
        
    # 2. Aggregation Pipeline
    pipeline = [
        {"$match": client_match},
        {
            "$lookup": {
                "from": "orders",
                "localField": "client_id",
                "foreignField": "client_id",
                "as": "order"
            }
        },
        # Join clients with their orders; keep clients with 0 orders (placeholder row)
        {"$unwind": {"path": "$order", "preserveNullAndEmptyArrays": True}},
        {
            "$lookup": {
                "from": "payments",
                "localField": "order.order_id",
                "foreignField": "order_id",
                "as": "p_list"
            }
        },
        {
            "$addFields": {
                # Aggregate payment phases in DB
                "p1": {"$filter": {"input": "$p_list", "as": "p", "cond": {"$eq": ["$$p.phase", 1]}}},
                "p2": {"$filter": {"input": "$p_list", "as": "p", "cond": {"$eq": ["$$p.phase", 2]}}},
                "p3": {"$filter": {"input": "$p_list", "as": "p", "cond": {"$eq": ["$$p.phase", 3]}}}
            }
        },
        {
            "$project": {
                "_id": 0,
                "order_db_id": {"$toString": "$order._id"},
                "order_id": "$order.order_id",
                "s_no": "$order.s_no",
                "order_date": "$order.order_date",
                "client_id": "$client_id",
                "client_country": "$country",
                "client_Email": "$email",
                "client_whatsapp_number": "$whatsapp_no",
                "reference_id": "$order.reference_id",
                "ref_no": {"$ifNull": ["$order.client_ref_no", "$client_ref_no"]},
                "manuscript_id": "$order.manuscript_id",
                "journal_name": "$order.journal_name",
                "title": "$order.title",
                "order_type": "$order.order_type",
                "index": "$order.index",
                "rank": "$order.rank",
                "currency": {"$ifNull": ["$order.currency", "USD"]},
                "total_amount": {"$ifNull": ["$order.total_amount", 0.0]},
                "writing_amount": {"$ifNull": ["$order.writing_amount", 0.0]},
                "modification_amount": {"$ifNull": ["$order.modification_amount", 0.0]},
                "po_amount": {"$ifNull": ["$order.po_amount", 0.0]},
                "writing_start_date": "$order.writing_start_date",
                "writing_end_date": "$order.writing_end_date",
                "modification_start_date": "$order.modification_start_date",
                "modification_end_date": "$order.modification_end_date",
                "po_start_date": "$order.po_start_date",
                "po_end_date": "$order.po_end_date",
                "phase": {"$literal": None},
                "phase_1_payment": {"$sum": "$p1.amount"},
                "phase_1_payment_date": {"$arrayElemAt": ["$p1.payment_date", 0]},
                "phase_2_payment": {"$sum": "$p2.amount"},
                "phase_2_payment_date": {"$arrayElemAt": ["$p2.payment_date", 0]},
                "phase_3_payment": {"$sum": "$p3.amount"},
                "phase_3_payment_date": {"$arrayElemAt": ["$p3.payment_date", 0]},
                "payment_status": {"$ifNull": ["$order.payment_status", "No Order"]},
                "amount": {"$ifNull": ["$order.amount", 0.0]},
                "client_link": "$client_link",
                "bank_account": "$bank_account",
                "client_affiliations": "$affiliation",
                "client_handler": "$client_handler",
                "remarks": {"$ifNull": ["$order.remarks", "No active orders for this client"]},
                "order_status": "$order.order_status"
            }
        }
    ]
    
    dashboard_data = list(clients_collection.aggregate(pipeline))
    
    # Resolve handler names for display
    for d in dashboard_data:
        resolve_client_handler(d)
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "Dashboard data fetched successfully",
        "data": dashboard_data
    }

@app.patch("/dashboard/orders/{order_db_id}", response_model=ApiResponse[dict])
def update_dashboard_order(order_db_id: str, update_data: DashboardUpdate, current_user: dict = Depends(get_current_user)):
    """
    Unified update endpoint for the dashboard using Order Database ID (Hex).
    Updates relevant collections based on provided fields.
    """
    # 1. Map fields to collections
    update_dict = update_data.model_dump(exclude_unset=True)
    if not update_dict:
        return {
            "status_code": 200,
            "status": "success",
            "message": "No changes provided",
            "data": None
        }

    # 2. Map fields to collections
    client_fields = ["client_id", "client_country", "client_Email", "client_whatsapp_number", "client_link", "bank_account", "client_affiliations"]
    order_fields = ["manuscript_id", "order_date", "reference_id", "ref_no", "journal_name", "title", "order_type", "index", "rank", "currency", "total_amount", "writing_amount", "modification_amount", "po_amount", "writing_start_date", "writing_end_date", "modification_start_date", "modification_end_date", "po_start_date", "po_end_date", "payment_status", "remarks"]
    payment_fields = ["phase_1_payment", "phase_1_payment_date", "phase_2_payment", "phase_2_payment_date", "phase_3_payment", "phase_3_payment_date"]

    # Get the order to verify it exists and find linked client
    try:
        order = orders_collection.find_one({"_id": ObjectId(order_db_id)})
    except Exception:
         raise HTTPException(status_code=400, detail="Invalid order_db_id format")
         
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    old_client_id = order["client_id"]
    order_custom_id = order["order_id"] # Internal ORD-xxx string

    # 3. Perform Updates
    
    # Update Clients & Handle client_id change
    client_updates = {f: update_dict[f] for f in client_fields if f in update_dict}
    if client_updates:
        # Check if client_id itself is changing
        new_client_id = client_updates.get("client_id")
        
        # Map dashboard field names back to client collection names
        mapped_client_updates = {}
        mapping = {
            "client_id": "client_id",
            "client_country": "country",
            "client_Email": "email",
            "client_whatsapp_number": "whatsapp_no",
            "client_link": "client_link",
            "bank_account": "bank_account",
            "client_affiliations": "affiliation"
        }
        for k, v in client_updates.items():
            mapped_client_updates[mapping.get(k, k)] = v

        # Update the client record
        clients_collection.update_one({"client_id": old_client_id}, {"$set": mapped_client_updates})

        # If client_id changed, ripple to all related collections
        if new_client_id and new_client_id != old_client_id:
            orders_collection.update_many({"client_id": old_client_id}, {"$set": {"client_id": new_client_id}})
            payments_collection.update_many({"client_id": old_client_id}, {"$set": {"client_id": new_client_id}})
            manuscripts_collection.update_many({"client_id": old_client_id}, {"$set": {"client_id": new_client_id}})
            # Update local variable for subsequent order updates in this same request
            old_client_id = new_client_id

    # Update Orders
    order_updates = {f: update_dict[f] for f in order_fields if f in update_dict}
    if order_updates:
        # Map dashboard field names back to order collection names if different
        mapped_order_updates = {}
        mapping = {"ref_no": "client_ref_no"}
        for k, v in order_updates.items():
            mapped_order_updates[mapping.get(k, k)] = v
        mapped_order_updates["updated_at"] = datetime.utcnow()
        orders_collection.update_one({"_id": ObjectId(order_db_id)}, {"$set": mapped_order_updates})

    # Update Payments
    payment_updates_raw = {f: update_dict[f] for f in payment_fields if f in update_dict}
    if payment_updates_raw:
        # Group by phase
        for phase in [1, 2, 3]:
            amt_key = f"phase_{phase}_payment"
            date_key = f"phase_{phase}_payment_date"
            
            p_updates = {}
            if amt_key in payment_updates_raw:
                p_updates["amount"] = payment_updates_raw[amt_key]
            if date_key in payment_updates_raw:
                p_updates["payment_date"] = payment_updates_raw[date_key]
            
            if p_updates:
                payments_collection.update_one(
                    {"order_id": order_custom_id, "phase": phase},
                    {"$set": p_updates},
                    upsert=True # Create if doesn't exist for that phase
                )

    return {
        "status_code": 200,
        "status": "success",
        "message": "Dashboard order updated successfully",
        "data": None
    }

# --- UNIFIED CREATE API ---

@app.post("/unified/create", response_model=ApiResponse[dict], status_code=status.HTTP_201_CREATED)
def create_unified_record(request: UnifiedCreateRequest, current_user: dict = Depends(get_current_user)):
    """
    Unified API to create client, order, manuscript, and payment records in one request.
    Accessible to all roles (Employee, Manager, Admin).

    Features:
    - Creates client if doesn't exist, updates if exists
    - Always creates order with unique reference_id
    - Optionally creates manuscript linked to client and order
    - Optionally creates payment record
    - payment_drive_link flows from client to order automatically
    """

    # Step 1: Handle Client Creation/Update
    existing_client = clients_collection.find_one({"client_id": request.client_id})

    if not existing_client:
        # Create new client
        client_data = {
            "client_id": request.client_id,
            "name": request.client_name,
            "country": request.client_country,
            "email": request.client_email,
            "whatsapp_no": request.client_whatsapp_no,
            "client_ref_no": request.client_ref_no,
            "client_link": request.client_link,
            "bank_account": request.client_bank_account,
            "affiliation": request.client_affiliation,
            "clients_details": request.clients_details,
            "client_drive_link": request.client_drive_link,
            "payment_drive_link": request.payment_drive_link,  # Store in client as source
            "total_orders": 0,
            "client_handler": current_user.get("email") if current_user["role"] == UserRole.EMPLOYEE else request.client_handler,
            "created_at": datetime.utcnow()
        }
        clients_collection.insert_one(client_data)
        client_id = request.client_id
        client_payment_drive_link = request.payment_drive_link
    else:
        # Use existing client, do not update fields
        client_id = existing_client["client_id"]
        
        # Get the current payment_drive_link from existing client
        client_payment_drive_link = existing_client.get("payment_drive_link")

    # Step 2: Create Manuscript (Optional)
    manuscript_id = None
    if request.create_manuscript and request.manuscript_title:
        manuscript_data = {
            "manuscript_id": f"MS-{client_id}-{request.reference_id}",
            "title": request.manuscript_title,
            "journal_name": request.manuscript_journal_name or request.journal_name,
            "order_type": request.order_type,
            "client_id": client_id,
            "created_at": datetime.utcnow()
        }
        manuscripts_collection.insert_one(manuscript_data)
        manuscript_id = manuscript_data["manuscript_id"]

    # Step 3: Create Order
    # Generate unique order_id
    global_order_count = orders_collection.count_documents({}) + 1
    order_id = f"ORD-{datetime.utcnow().strftime('%Y')}-{global_order_count:03d}"
    
    # Ensure uniqueness in case of deleted documents mapping to same count
    while orders_collection.find_one({"order_id": order_id}):
        global_order_count += 1
        order_id = f"ORD-{datetime.utcnow().strftime('%Y')}-{global_order_count:03d}"

    # Ensure reference_id is unique
    if orders_collection.find_one({"reference_id": request.reference_id}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Reference ID '{request.reference_id}' already exists"
        )

    order_data = {
        "order_id": order_id,
        "reference_id": request.reference_id,
        "profile_name": request.profile_name,
        "client_ref_no": request.client_ref_no,
        "s_no": global_order_count,
        "order_date": request.order_date,
        "client_id": client_id,
        "manuscript_id": manuscript_id,
        "journal_name": request.journal_name,
        "title": request.title,
        "order_type": request.order_type,
        "index": request.index,
        "rank": request.rank,
        "currency": request.currency,
        "total_amount": 0,  # Will be updated if payment is created
        "writing_amount": 0,
        "modification_amount": 0,
        "po_amount": 0,
        "writing_start_date": request.write_start_date,
        "writing_end_date": None,
        "modification_start_date": None,
        "modification_end_date": None,
        "po_start_date": None,
        "po_end_date": None,
        "payment_status": request.payment_status,
        "payment_drive_link": client_payment_drive_link,  # From client's payment_drive_link
        "remarks": None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    orders_collection.insert_one(order_data)

    # Step 4: Create Payment (Optional)
    payment_created = False
    if request.create_payment and request.payment_amount:
        payment_data = {
            "client_ref_number": request.client_ref_no,
            "reference_id": request.reference_id,
            "client_id": client_id,
            "order_id": order_id,
            "phase": request.payment_phase or 1,
            "amount": request.payment_amount,
            "payment_received_account": request.payment_received_account,
            "payment_date": request.payment_date or datetime.utcnow().date().isoformat(),
            "status": "paid",
            "created_at": datetime.utcnow()
        }

        # Update phase-specific fields
        phase = payment_data["phase"]
        payment_data[f"phase_{phase}_payment"] = request.payment_amount
        payment_data[f"phase_{phase}_payment_date"] = payment_data["payment_date"]

        payments_collection.insert_one(payment_data)

        # Update order total_amount
        orders_collection.update_one(
            {"order_id": order_id},
            {"$set": {"total_amount": request.payment_amount}}
        )
        payment_created = True

    # Step 5: Update Client Order Count
    clients_collection.update_one(
        {"client_id": client_id},
        {"$inc": {"total_orders": 1}}
    )

    # Return comprehensive response
    return {
        "status_code": 201,
        "status": "success",
        "message": "Unified record created successfully",
        "data": {
            "client_id": client_id,
            "order_id": order_id,
            "reference_id": request.reference_id,
            "manuscript_id": manuscript_id,
            "payment_created": payment_created,
            "client_created": existing_client is None,
            "created_records": {
                "client": existing_client is None,
                "order": True,
                "manuscript": request.create_manuscript,
                "payment": payment_created
            },
            "payment_drive_link_used": client_payment_drive_link
        }
    }


