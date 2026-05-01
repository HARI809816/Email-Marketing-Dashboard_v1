from typing import Optional, Any
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
    ProfileUpdate,
    DashboardUpdate,
    ApiResponse,
    ClientAssignRequest,
    UnifiedCreateRequest
)
import random
import smtplib
from email.message import EmailMessage
import time
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import (
    SMTP_SERVER, 
    SMTP_PORT, 
    SMTP_USERNAME, 
    SMTP_PASSWORD, 
    EMAIL_FROM,
    ALLOWED_ORIGINS
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

from app.currency_converter import convert_inr_to_usd, convert_usd_to_inr, get_current_rate_info
from bson import ObjectId


app = FastAPI(title="Email Dashboard API")

# --- CORS CONFIGURATION ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# --- PERFORMANCE MONITORING MIDDLEWARE ---
class PerformanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Log slow requests (>1 second)
        if process_time > 1.0:
            print(f"🐌 SLOW REQUEST: {request.method} {request.url.path} took {process_time:.2f}s")
        elif process_time > 0.5:
            print(f"⚠️  MEDIUM REQUEST: {request.method} {request.url.path} took {process_time:.2f}s")
        
        return response

app.add_middleware(PerformanceMiddleware)


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
# --- HELPER ---
# --- HELPER ---
async def send_otp_email(to_email: str, otp: str):
    """
    Sends an OTP email via SMTP asynchronously.
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
        import aiosmtplib
    except ImportError as e:
        print(f"AIOSMTPLIB_MISSING: {e}")
        return False

    try:
        if SMTP_PORT == 465:
            # Port 465 requires SMTP_SSL from the start
            server = aiosmtplib.SMTP(hostname=SMTP_SERVER, port=SMTP_PORT, use_tls=True)
            await server.connect()
            await server.login(SMTP_USERNAME, SMTP_PASSWORD)
            await server.send_message(msg)
            await server.quit()
        else:
            # Port 587 (and others) typically use STARTTLS
            server = aiosmtplib.SMTP(hostname=SMTP_SERVER, port=SMTP_PORT)
            await server.connect()
            await server.start_tls()
            await server.login(SMTP_USERNAME, SMTP_PASSWORD)
            await server.send_message(msg)
            await server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def format_mongo_id(doc):
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc

def parse_date(date_str: Any) -> Optional[datetime]:
    """Helper to convert string dates to datetime objects for MongoDB."""
    if not date_str:
        return None
    if isinstance(date_str, datetime):
        return date_str
    try:
        # Handle simple date strings like "2024-01-01"
        if len(date_str) == 10:
            return datetime.strptime(date_str, "%Y-%m-%d")
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except Exception:
        return None

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

def resolve_client_handler_bulk(clients: list[dict]) -> list[dict]:
    """Resolve handler names for many clients with a single DB query."""
    emails = {client.get("client_handler") for client in clients if client.get("client_handler")}
    if not emails:
        for client in clients:
            client["client_handler_name"] = None
        return clients

    handlers = users_collection.find({"email": {"$in": list(emails)}}, {"email": 1, "full_name": 1})
    email_to_name = {handler["email"]: handler.get("full_name") for handler in handlers}
    for client in clients:
        handler_email = client.get("client_handler")
        if handler_email:
            client["client_handler_name"] = email_to_name.get(handler_email, handler_email)
        else:
            client["client_handler_name"] = None
    return clients

def get_user_email_by_name(name_or_email: str) -> str:
    """
    Finds a user's email by their full name or returns the input if it's already an email.
    """
    if not name_or_email:
        return name_or_email
        
    # If it looks like an email, return as is
    if "@" in name_or_email:
        return name_or_email
        
    # Try to find user by full name
    user = users_collection.find_one({"full_name": name_or_email})
    if user:
        return user.get("email")
        
    return name_or_email


@app.get("/", response_model=ApiResponse[dict])
def read_root():
    return {
        "status_code": 200,
        "status": "success",
        "message": "Welcome to Email Dashboard API",
        "data": None
    }

# --- CURRENCY CONVERSION ---

@app.get("/currency/exchange-rate", response_model=ApiResponse[dict])
def get_exchange_rate():
    """
    Get current INR to USD exchange rate with caching.
    """
    rate_info = get_current_rate_info()
    if not rate_info:
        raise HTTPException(
            status_code=503,
            detail="Exchange rate service unavailable"
        )
    return {
        "status_code": 200,
        "status": "success",
        "message": "Exchange rate fetched successfully",
        "data": rate_info
    }

@app.post("/currency/inr-to-usd", response_model=ApiResponse[dict])
def convert_inr_to_usd_endpoint(amount: dict):
    """
    Convert amount from INR to USD.
    Request: {"amount_inr": 1000}
    Response includes current rate and converted amount.
    """
    amount_inr = amount.get("amount_inr")
    if amount_inr is None or amount_inr < 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid amount_inr. Must be a positive number."
        )
    
    result = convert_inr_to_usd(float(amount_inr))
    if not result:
        raise HTTPException(
            status_code=503,
            detail="Exchange rate service unavailable"
        )
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "Conversion completed successfully",
        "data": result
    }

@app.post("/currency/usd-to-inr", response_model=ApiResponse[dict])
def convert_usd_to_inr_endpoint(amount: dict):
    """
    Convert amount from USD to INR.
    Request: {"amount_usd": 15}
    Response includes current rate and converted amount.
    """
    amount_usd = amount.get("amount_usd")
    if amount_usd is None or amount_usd < 0:
        raise HTTPException(
            status_code=400,
            detail="Invalid amount_usd. Must be a positive number."
        )
    
    result = convert_usd_to_inr(float(amount_usd))
    if not result:
        raise HTTPException(
            status_code=503,
            detail="Exchange rate service unavailable"
        )
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "Conversion completed successfully",
        "data": result
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
async def login(request: LoginRequest):
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
        
        # Send OTP asynchronously
        sent = await send_otp_email(user["email"], otp)
        
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
        # Decrypt password for display
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
        # Decrypt password for display
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

@app.post("/users/profiles/append", response_model=ApiResponse[dict])
def append_profile_name(data: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    """Append a new profile name to a user's list."""
    target_user = users_collection.find_one({"email": data.email})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    users_collection.update_one(
        {"email": data.email},
        {"$addToSet": {"profile_names": data.profile_name}}
    )
    return {
        "status_code": 200,
        "status": "success",
        "message": f"Profile '{data.profile_name}' added to {data.email}",
        "data": None
    }

# @app.put("/users/profiles/update", response_model=ApiResponse[dict])
# def update_profile_name(data: ProfileUpdate, current_user: dict = Depends(require_manager_or_higher)):
#     """Update an existing profile name in a user's list."""
#     if not data.new_profile_name:
#         raise HTTPException(status_code=400, detail="new_profile_name is required for update")
        
#     target_user = users_collection.find_one({"email": data.email})
#     if not target_user:
#         raise HTTPException(status_code=404, detail="User not found")
        
#     # Atomic update of specific element in array
#     result = users_collection.update_one(
#         {"email": data.email, "profile_names": data.profile_name},
#         {"$set": {"profile_names.$": data.new_profile_name}}
#     )
    
#     if result.matched_count == 0:
#         raise HTTPException(status_code=404, detail=f"Profile '{data.profile_name}' not found for this user")

#     return {
#         "status_code": 200,
#         "status": "success",
#         "message": f"Profile '{data.profile_name}' updated to '{data.new_profile_name}'",
#         "data": None
#     }

# @app.delete("/users/profiles/remove", response_model=ApiResponse[dict])
# def remove_profile_name(data: ProfileUpdate, current_user: dict = Depends(require_manager_or_higher)):
#     """Remove a profile name from a user's list."""
#     target_user = users_collection.find_one({"email": data.email})
#     if not target_user:
#         raise HTTPException(status_code=404, detail="User not found")
    
#     users_collection.update_one(
#         {"email": data.email},
#         {"$pull": {"profile_names": data.profile_name}}
#     )
#     return {
#         "status_code": 200,
#         "status": "success",
#         "message": f"Profile '{data.profile_name}' removed from {data.email}",
#         "data": None
#     }


def get_user_dashboard_data(client_match: dict):
    """
    Common logic to fetch dashboard stats, country stats, and order status details
    based on a client filter (e.g., all clients for Admin, or specific handler for Employee).
    """
    from app.currency_converter import get_inr_to_usd_rate
    rate = get_inr_to_usd_rate() or 0.012

    # 2. Aggregation Pipeline to fetch clients and their stats in ONE go
    pipeline = [
        {"$match": client_match},
        {
            "$lookup": {
                "from": "orders",
                "let": {"cid": "$client_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$client_id", "$$cid"]}}},
                    {
                        "$lookup": {
                            "from": "payments",
                            "localField": "order_id",
                            "foreignField": "order_id",
                            "as": "order_payments"
                        }
                    },
                    {
                        "$addFields": {
                            "order_total_usd": {
                                "$cond": [
                                    {"$eq": ["$currency", "INR"]},
                                    {"$multiply": ["$total_amount", rate]},
                                    "$total_amount"
                                ]
                            },
                            "order_paid": {
                                "$sum": {
                                    "$map": {
                                        "input": "$order_payments",
                                        "as": "p",
                                        "in": {
                                            "$cond": [
                                                {"$eq": ["$currency", "INR"]},
                                                {"$multiply": [{"$ifNull": ["$$p.paid_amount", 0.0]}, rate]},
                                                {"$ifNull": ["$$p.paid_amount", 0.0]}
                                            ]
                                        }
                                    }
                                }
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "total_amount": {"$sum": "$order_total_usd"},
                            "paid_amount": {"$sum": "$order_paid"},
                            "order_count": {"$sum": 1},
                            "payment_status": {"$first": "$payment_status"},
                            "paid_order_count": {
                                "$sum": {
                                    "$cond": [
                                        {"$gt": ["$order_paid", 0]},
                                        1, 0
                                    ]
                                }
                            },
                            "pending_order_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$eq": ["$order_paid", 0]},
                                                {"$ne": ["$order_status", "Inactive"]}
                                            ]
                                        },
                                        1, 0
                                    ]
                                }
                            },
                            "reject_order_count": {
                                "$sum": {"$cond": [{"$eq": ["$order_status", "Inactive"]}, 1, 0]}
                            },
                            "orders_list": {
                                "$push": {
                                    "client_id": "$client_id",
                                    "reference_id": "$reference_id",
                                    "order_status": "$order_status",
                                    "paid_amount": "$order_paid",
                                    "payment_status": {
                                        "$cond": [
                                            {"$gte": ["$order_paid", "$order_total_usd"]},
                                            "Paid",
                                            {
                                                "$cond": [
                                                    {"$gt": ["$order_paid", 0]},
                                                    "Partial Paid",
                                                    "Pending"
                                                ]
                                            }
                                        ]
                                    },
                                    "created_at": "$created_at",
                                    "order_date": "$order_date"
                                }
                            }
                        }
                    }
                ],
                "as": "stats"
            }
        },
        {"$unwind": {"path": "$stats", "preserveNullAndEmptyArrays": True}},
        {
            "$addFields": {
                "total_amount": {"$ifNull": ["$stats.total_amount", 0.0]},
                "paid_amount": {"$ifNull": ["$stats.paid_amount", 0.0]},
                "order_count": {"$ifNull": ["$stats.order_count", 0]},
                "paid_order_count": {"$ifNull": ["$stats.paid_order_count", 0]},
                "pending_order_count": {"$ifNull": ["$stats.pending_order_count", 0]},
                "reject_order_count": {"$ifNull": ["$stats.reject_order_count", 0]},
                "orders_list": {"$ifNull": ["$stats.orders_list", []]}
            }
        },
        {"$project": {"stats": 0}}
    ]
    
    clients_with_stats = list(clients_collection.aggregate(pipeline))
    
    country_stats_map = {}
    order_status_details = []
    
    total_system_amount = 0.0
    total_system_paid = 0.0
    total_system_orders = 0
    total_system_pending = 0
    total_system_rejects = 0
    
    for c in clients_with_stats:
        country = c.get("country") or "Unknown"
        if country not in country_stats_map:
            country_stats_map[country] = {
                "country_name": country,
                "client_count": 0,
                "order_count": 0,
                "paid_count": 0,
                "paid_amount": 0.0,
                "pending_count": 0,
                "reject_count": 0
            }
        
        country_stats_map[country]["client_count"] += 1
        country_stats_map[country]["order_count"] += c.get("order_count", 0)
        country_stats_map[country]["paid_count"] += c.get("paid_order_count", 0)
        country_stats_map[country]["paid_amount"] = round(country_stats_map[country]["paid_amount"] + c.get("paid_amount", 0.0), 2)
        country_stats_map[country]["pending_count"] += c.get("pending_order_count", 0)
        country_stats_map[country]["reject_count"] += c.get("reject_order_count", 0)
        
        for order in c.get("orders_list", []):
            order_status_details.append({
                "client_name": c.get("name"),
                "client_id": order.get("client_id"),
                "reference_id": order.get("reference_id"),
                "order_status": order.get("order_status"),
                "payment_status": order.get("payment_status"),
                "country": c.get("country"),        
                "paid_amount": round(order.get("paid_amount", 0.0), 2),
                "created_at": order.get("created_at"),
                "order_date": order.get("order_date")
            })
            
        total_system_amount += c.get("total_amount", 0.0)
        total_system_paid += c.get("paid_amount", 0.0)
        total_system_orders += c.get("order_count", 0)
        total_system_pending += c.get("pending_order_count", 0)
        total_system_rejects += c.get("reject_order_count", 0)

    total_clients_count = len(clients_with_stats)
    pending_pct = (total_system_pending / total_system_orders * 100) if total_system_orders > 0 else 0.0
    
    dashboard_stats = {
        "total_amount": round(total_system_amount, 2),
        "paid_amount": round(total_system_paid, 2),
        "remaining_amount": round(total_system_amount - total_system_paid, 2),
        "total_clients": total_clients_count,
        "total_clients_percentage": 100.0, 
        "pending_count": total_system_pending,
        "pending_count_percentage": round(pending_pct, 1),
        "reject_count": total_system_rejects, 
        "reject_count_percentage": round((total_system_rejects / total_system_orders * 100), 1) if total_system_orders > 0 else 0.0
    }
    
    country_based_details = list(country_stats_map.values())
    country_split = {c["country_name"]: c["paid_amount"] for c in country_based_details}
    
    return {
        "dashboard_stats": dashboard_stats,
        "country_based_details": country_based_details,
        "country_split": country_split,
        "order_status_details": order_status_details
    }


@app.get("/users/me/details", response_model=ApiResponse[UserDetailResponse])
def get_own_details(current_user: dict = Depends(get_current_user)):
    """
    Get current user profile details including country stats and order statuses.
    """
    # 1. Determine client filter (Admin/Manager see all, Employee see only their own)
    client_match = {}
    if current_user["role"] not in [UserRole.ADMIN, UserRole.MANAGER]:
        client_match = {"client_handler": current_user.get("email")}

    # 2. Fetch data using helper
    dashboard_data = get_user_dashboard_data(client_match)
    
    # 3. Format user profile
    user_data = format_mongo_id(current_user.copy())
    user_data["password"] = decrypt_password(user_data.get("password", ""))
    
    # 4. Merge dashboard data into user response
    user_data.update(dashboard_data)
    
    return {
        "status_code": 200,
        "status": "success",
        "message": "User details fetched successfully",
        "data": user_data
    }

@app.get("/users/{email}/details", response_model=ApiResponse[UserDetailResponse])
def get_user_details(email: str, current_user: dict = Depends(require_manager_or_higher)):
    """
    Get profile details of any user including country stats and order statuses.
    Restricted to Admin and Manager.
    """
    target_user = users_collection.find_one({"email": email})
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    # 1. Determine client filter (Admin/Manager viewing an employee - filter by that employee's clients)
    client_match = {"client_handler": target_user.get("email")}

    # 2. Fetch data using helper
    dashboard_data = get_user_dashboard_data(client_match)
    
    # 3. Format target user profile
    user_data = format_mongo_id(target_user)
    user_data["password"] = decrypt_password(user_data.get("password", ""))
    
    # 4. Merge dashboard data into user response
    user_data.update(dashboard_data)
    
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
    if client_dict.get("client_handler"):
        client_dict["client_handler"] = get_user_email_by_name(client_dict["client_handler"])
    else:
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
    clients = [format_mongo_id(c) for c in clients_collection.find(query)]
    resolved = resolve_client_handler_bulk(clients)
    
    if current_user["role"] == UserRole.EMPLOYEE:
        employee_names = {current_user.get("full_name")}
        profile_names = set(current_user.get("profile_names", []))
    else:
        # Optimized retrieval of employee and profile names using projection and efficient sets
        employees_data = list(users_collection.find(
            {"role": UserRole.EMPLOYEE}, 
            {"full_name": 1, "profile_names": 1, "_id": 0}
        ))
        employee_names = {emp["full_name"] for emp in employees_data if emp.get("full_name")}
        profile_names = {
            p for emp in employees_data 
            if isinstance(emp.get("profile_names"), list) 
            for p in emp["profile_names"]
        }
                
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
        my_client_ids = clients_collection.distinct("client_id", {"client_handler": current_user.get("email")})
        query = {"client_id": {"$in": my_client_ids}} if my_client_ids else {"client_id": {"$in": []}}
        
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
        my_client_ids = clients_collection.distinct("client_id", {"client_handler": current_user.get("email")})
        query = {"client_id": {"$in": my_client_ids}} if my_client_ids else {"client_id": {"$in": []}}
        
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
        my_client_ids = clients_collection.distinct("client_id", {"client_handler": current_user.get("email")})
        query = {"client_id": {"$in": my_client_ids}} if my_client_ids else {"client_id": {"$in": []}}
        
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
    Includes caching for improved performance.
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
            "$project": {
                "_id": 0,
                "order_db_id": {"$cond": [{"$ifNull": ["$order._id", False]}, {"$toString": "$order._id"}, None]},
                "order_id": "$order.order_id",
                "s_no": "$order.s_no",
                "order_date": "$order.order_date",
                "client_id": "$client_id",
                "client_name": "$name",
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
                # All phase fields live in a single payment doc per order — read directly
                "phase_1_payment": {"$arrayElemAt": ["$p_list.phase_1_payment", 0]},
                "phase_1_payment_date": {"$arrayElemAt": ["$p_list.phase_1_payment_date", 0]},
                "phase_1_payment_details": {"$arrayElemAt": ["$p_list.phase_1_payment_details", 0]},
                "phase_2_payment": {"$arrayElemAt": ["$p_list.phase_2_payment", 0]},
                "phase_2_payment_date": {"$arrayElemAt": ["$p_list.phase_2_payment_date", 0]},
                "phase_2_payment_details": {"$arrayElemAt": ["$p_list.phase_2_payment_details", 0]},
                "phase_3_payment": {"$arrayElemAt": ["$p_list.phase_3_payment", 0]},
                "phase_3_payment_date": {"$arrayElemAt": ["$p_list.phase_3_payment_date", 0]},
                "phase_3_payment_details": {"$arrayElemAt": ["$p_list.phase_3_payment_details", 0]},
                "payment_status": {"$ifNull": ["$order.payment_status", "No Order"]},
                "paid_amount": {"$ifNull": ["$order.paid_amount", 0.0]},
                "client_link": "$client_link",
                "bank_account": "$bank_account",
                "client_affiliations": "$affiliation",
                "client_handler": "$client_handler",
                "remarks": "$order.remarks",
                "order_status": "$order.order_status",
                "clients_details": "$order.clients_details",
                "client_drive_link": {"$ifNull": ["$order.client_drive_link", "$client_drive_link"]},
                "payment_drive_link": {"$ifNull": ["$order.payment_drive_link", "$payment_drive_link"]}
            }
        }
    ]
    
    dashboard_data = list(clients_collection.aggregate(pipeline))
    
    # Resolve handler names for display in bulk
    resolve_client_handler_bulk(dashboard_data)
    
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
    order_fields = ["manuscript_id", "order_date", "reference_id", "ref_no", "journal_name", "title", "order_type", "index", "rank", "currency", "total_amount", "writing_amount", "modification_amount", "po_amount", "writing_start_date", "writing_end_date", "modification_start_date", "modification_end_date", "po_start_date", "po_end_date", "payment_status", "remarks", "order_status", "payment_drive_link", "paid_amount", "clients_details", "client_details", "client_drive_link"]
    payment_fields = ["phase_1_payment", "phase_1_payment_date", "phase_1_payment_details", "phase_2_payment", "phase_2_payment_date", "phase_2_payment_details", "phase_3_payment", "phase_3_payment_date", "phase_3_payment_details","payment_status", "paid_amount"]

    # Get the order to verify it exists and find linked client
    try:
        order = orders_collection.find_one({"_id": ObjectId(order_db_id)})
    except Exception:
         raise HTTPException(status_code=400, detail="Invalid order_db_id format")
         
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    old_client_id = order["client_id"]
    order_custom_id = order["order_id"]

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
        mapping = {"ref_no": "client_ref_no", "client_details": "clients_details"}
        for k, v in order_updates.items():
            mapped_order_updates[mapping.get(k, k)] = v
        mapped_order_updates["updated_at"] = datetime.utcnow()
        orders_collection.update_one({"_id": ObjectId(order_db_id)}, {"$set": mapped_order_updates})

    # Update Payments — direct field update (same pattern as orders/clients)
    payment_updates_raw = {f: update_dict[f] for f in payment_fields if f in update_dict}
    if payment_updates_raw:
        payments_collection.update_one(
            {"order_id": order_custom_id},
            {"$set": payment_updates_raw},
            upsert=True
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
            "payment_drive_link": request.payment_drive_link,
            "client_drive_link": request.client_drive_link,
            "total_orders": 0,
            "client_handler": current_user.get("email") if current_user["role"] == UserRole.EMPLOYEE else get_user_email_by_name(request.client_handler),
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
        "order_date": parse_date(request.order_date),
        "client_id": client_id,
        "manuscript_id": manuscript_id,
        "journal_name": request.journal_name,
        "title": request.title,
        "order_type": request.order_type,
        "index": request.index,
        "rank": request.rank,
        "currency": request.currency or "USD",
        "total_amount": request.total_amount or 0,
        "writing_amount": request.writing_amount or 0,
        "modification_amount": request.modification_amount or 0,
        "po_amount": request.po_amount or 0,
        "writing_start_date": parse_date(request.writing_start_date) or parse_date(request.write_start_date),
        "writing_end_date": parse_date(request.writing_end_date),
        "modification_start_date": parse_date(request.modification_start_date),
        "modification_end_date": parse_date(request.modification_end_date),
        "po_start_date": parse_date(request.po_start_date),
        "po_end_date": parse_date(request.po_end_date),
        "payment_status": request.payment_status or "Pending",
        "order_status": "Active",
        "payment_drive_link": request.payment_drive_link or client_payment_drive_link,
        "clients_details": request.clients_details or getattr(request, 'client_details', None),
        "client_drive_link": request.client_drive_link,
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
            "payment_date": parse_date(request.payment_date) or datetime.utcnow(),
            "status": "paid",
            "paid_amount": request.payment_amount,
            "created_at": datetime.utcnow()
        }

        # Update phase-specific fields
        phase = payment_data["phase"]
        payment_data[f"phase_{phase}_payment"] = request.payment_amount
        payment_data[f"phase_{phase}_payment_date"] = payment_data["payment_date"]

        payments_collection.insert_one(payment_data)

        # Update order total_amount only if it wasn't already set from request
        if not order_data.get("total_amount"):
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


