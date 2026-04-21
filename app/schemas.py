from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional, Generic, TypeVar, Any
from enum import Enum
from datetime import datetime

class UserRole(str, Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    status_code: int
    status: str
    message: str
    data: Optional[T] = None
    detail: Optional[Any] = None

class PasswordUpdate(BaseModel):
    new_password: str

class AdminPasswordUpdate(BaseModel):
    email: EmailStr
    new_password: str

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None
    profile_names: list[str] = Field(default_factory=list)  # Employees can maintain multiple profiles
    role: UserRole = UserRole.EMPLOYEE
    phone_number: Optional[str] = None
    permissions: Optional[dict[str, list[str]]] = Field(default_factory=lambda: {"dashboard": []})
    branch: Optional[str] = None

class UserCreate(UserBase):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone_number: Optional[str] = None
    branch: Optional[str] = None
    profile_name: Optional[str] = None
    password: str
    permissions: Optional[dict[str, list[str]]] = Field(default_factory=lambda: {"dashboard": []})
    role: UserRole = UserRole.MANAGER

class PermissionUpdate(BaseModel):
    email: EmailStr
    permissions: dict[str, list[str]]

class UserResponse(UserBase):
    id: str = Field(..., alias="_id")
    password: Optional[str] = None

    class Config:
        populate_by_name = True

class DashboardStats(BaseModel):
    overall_amount: float = 0.0
    overall_amount_percentage: float = 0.0
    total_clients: int = 0
    total_clients_percentage: float = 0.0
    pending_count: int = 0
    pending_count_percentage: float = 0.0
    reject_count: int = 0
    reject_count_percentage: float = 0.0

class UserDetailResponse(UserResponse):
    handled_clients: list[ClientDetailResponse] = []
    dashboard_stats: Optional[DashboardStats] = None
    country_split: dict[str, float] = {}

class Token(BaseModel):
    access_token: str
    token_type: str

class LoginResponse(BaseModel):
    access_token: Optional[str] = None
    token_type: Optional[str] = None
    otp_required: bool = False
    email: Optional[EmailStr] = None

class TokenData(BaseModel):
    email: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str

# --- SCHEMA FROM ERD ---

class ClientBase(BaseModel):
    client_id: str
    name: str
    country: Optional[str] = None
    email: Optional[EmailStr] = None
    whatsapp_no: Optional[str] = None
    client_ref_no: Optional[str] = None
    client_link: Optional[str] = None
    bank_account: Optional[str] = None
    affiliation: Optional[str] = None
    clients_details: Optional[str] = None  # New field for detailed client information
    client_drive_link: Optional[str] = None  # New field for client drive link
    payment_drive_link: Optional[str] = None  # New field - SOURCE for orders payment_drive_link
    total_orders: int = 0
    client_handler: Optional[str] = None  # Stores employee EMAIL (unique reference)
    client_handler_name: Optional[str] = None  # Resolved full name for display (not stored in DB)

class ClientCreate(ClientBase):
    pass

class ClientResponse(ClientBase):
    id: str = Field(..., alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    client_handler_name: Optional[str] = None  # Resolved full name
    class Config:
        populate_by_name = True

class ClientDetailResponse(ClientBase):
    id: str = Field(..., alias="_id")
    total_amount: float = 0.0
    writing_amount: float = 0.0
    modification_amount: float = 0.0
    po_amount: float = 0.0
    paid_amount: float = 0.0
    remaining_amount: float = 0.0
    payment_status: Optional[str] = "No Order"
    client_handler_name: Optional[str] = None  # Resolved full name
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True

class ClientAssignRequest(BaseModel):
    client_id: str
    employee_email: EmailStr

class ManuscriptBase(BaseModel):
    manuscript_id: str
    title: str
    journal_name: Optional[str] = None  # Target journal name
    order_type: Optional[str] = None
    client_id: str # Ref to Client

class ManuscriptCreate(ManuscriptBase):
    pass

class ManuscriptResponse(ManuscriptBase):
    id: str = Field(..., alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    class Config:
        populate_by_name = True

class OrderBase(BaseModel):
    order_id: str
    reference_id: str  # Unique per order — created by users (employees/admins)
    profile_name: Optional[str] = None # The specific profile used to handle this order
    client_ref_no: Optional[str] = None  # Optional — given by the client
    s_no: Optional[int] = None
    order_date: datetime = Field(default_factory=datetime.utcnow)
    client_id: str # Ref to Client
    manuscript_id: Optional[str] = None  # Optional — only ~30% of clients provide manuscripts
    journal_name: Optional[str] = None  # Target journal name
    title: Optional[str] = None  # Paper title
    order_type: Optional[str] = None
    index: Optional[str] = None
    rank: Optional[str] = None
    currency: str = "USD"
    total_amount: float = 0.0
    writing_amount: float = 0.0
    modification_amount: float = 0.0
    po_amount: float = 0.0
    writing_start_date: Optional[datetime] = None
    writing_end_date: Optional[datetime] = None
    modification_start_date: Optional[datetime] = None
    modification_end_date: Optional[datetime] = None
    po_start_date: Optional[datetime] = None
    po_end_date: Optional[datetime] = None
    payment_status: str = "Pending"
    remarks: Optional[str] = None

class OrderCreate(OrderBase):
    pass

class OrderResponse(OrderBase):
    id: str = Field(..., alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    class Config:
        populate_by_name = True

class PaymentBase(BaseModel):
    client_ref_number: Optional[str] = None
    reference_id: Optional[str] = None  # Copied from order for easy lookup
    client_id: str # Ref to Client
    phase: int = 1
    amount: float = 0.0
    payment_received_account: Optional[str] = None
    payment_date: Optional[datetime] = None
    phase_1_payment: Optional[float] = 0.0
    phase_1_payment_date: Optional[datetime] = None
    phase_2_payment: Optional[float] = 0.0
    phase_2_payment_date: Optional[datetime] = None
    phase_3_payment: Optional[float] = 0.0
    phase_3_payment_date: Optional[datetime] = None
    status: str = "Pending"

class PaymentCreate(PaymentBase):
    pass

class PaymentResponse(PaymentBase):
    id: str = Field(..., alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    class Config:
        populate_by_name = True

class DashboardOrderResponse(BaseModel):
    s_no: Optional[int] = None
    order_db_id: Optional[str] = None
    order_id: Optional[str] = None
    order_date: Optional[datetime] = None
    client_id: str
    client_country: Optional[str] = None
    client_Email: Optional[str] = None
    client_whatsapp_number: Optional[str] = None
    reference_id: Optional[str] = None
    ref_no: Optional[str] = None
    manuscript_id: Optional[str] = None
    journal_name: Optional[str] = None
    title: Optional[str] = None
    order_type: Optional[str] = None
    index: Optional[str] = None
    rank: Optional[str] = None
    currency: Optional[str] = "USD"
    total_amount: float = 0.0
    writing_amount: float = 0.0
    modification_amount: float = 0.0
    po_amount: float = 0.0
    writing_start_date: Optional[datetime] = None
    writing_end_date: Optional[datetime] = None
    modification_start_date: Optional[datetime] = None
    modification_end_date: Optional[datetime] = None
    po_start_date: Optional[datetime] = None
    po_end_date: Optional[datetime] = None
    phase: Optional[int] = None
    phase_1_payment: float = 0.0
    phase_1_payment_date: Optional[datetime] = None
    phase_2_payment: float = 0.0
    phase_2_payment_date: Optional[datetime] = None
    phase_3_payment: float = 0.0
    phase_3_payment_date: Optional[datetime] = None
    payment_status: Optional[str] = "Pending"
    client_link: Optional[str] = None
    bank_account: Optional[str] = None
    client_affiliations: Optional[str] = None
    client_handler: Optional[str] = None
    client_handler_name: Optional[str] = None
    remarks: Optional[str] = None

    @field_validator(
        "order_date", "writing_start_date", "writing_end_date", 
        "modification_start_date", "modification_end_date", 
        "po_start_date", "po_end_date", 
        "phase_1_payment_date", "phase_2_payment_date", "phase_3_payment_date", 
        mode="before"
    )
    @classmethod
    def empty_string_to_none(cls, v: Any) -> Any:
        if v == "":
            return None
        return v

class DashboardUpdate(BaseModel):
    # CLIENT FIELDS
    client_id: Optional[str] = None
    client_country: Optional[str] = None
    client_Email: Optional[EmailStr] = None
    client_whatsapp_number: Optional[str] = None
    client_link: Optional[str] = None
    bank_account: Optional[str] = None
    client_affiliations: Optional[str] = None
    
    # ORDER FIELDS
    manuscript_id: Optional[str] = None
    order_date: Optional[datetime] = None
    reference_id: Optional[str] = None
    ref_no: Optional[str] = None
    journal_name: Optional[str] = None
    title: Optional[str] = None
    order_type: Optional[str] = None
    index: Optional[str] = None
    rank: Optional[str] = None
    currency: Optional[str] = None
    total_amount: Optional[float] = None
    writing_amount: Optional[float] = None
    modification_amount: Optional[float] = None
    po_amount: Optional[float] = None
    writing_start_date: Optional[datetime] = None
    writing_end_date: Optional[datetime] = None
    modification_start_date: Optional[datetime] = None
    modification_end_date: Optional[datetime] = None
    po_start_date: Optional[datetime] = None
    po_end_date: Optional[datetime] = None
    payment_status: Optional[str] = None
    remarks: Optional[str] = None

    # PAYMENT FIELDS (Updates the first payment record for simplicity or we can expand)
    phase_1_payment: Optional[float] = None
    phase_1_payment_date: Optional[datetime] = None
    phase_2_payment: Optional[float] = None
    phase_2_payment_date: Optional[datetime] = None
    phase_3_payment: Optional[float] = None
    phase_3_payment_date: Optional[datetime] = None

# --- UNIFIED CREATE API SCHEMA ---

class UnifiedCreateRequest(BaseModel):
    """Unified schema for creating client, order, manuscript, and payment records in one API call"""

    # Client fields
    client_id: str
    client_name: str
    client_country: Optional[str] = None
    client_email: Optional[EmailStr] = None
    client_whatsapp_no: Optional[str] = None
    client_ref_no: Optional[str] = None
    client_link: Optional[str] = None
    client_bank_account: Optional[str] = None
    client_affiliation: Optional[str] = None
    clients_details: Optional[str] = None  # New field for detailed client information
    client_drive_link: Optional[str] = None  # New field for client drive link
    payment_drive_link: Optional[str] = None  # New field - stored in client, copied to orders

    # Order fields
    client_handler: Optional[str] = None  # For admin/manager to assign a handler
    order_date: Optional[str] = None
    reference_id: str
    profile_name: str  # From user profile
    title: str
    order_type: str  # writing | modification | proofreading
    index: str  # SCI | Scopus | ESCI
    rank: str  # Q1 | Q2 | Q3 | Q4
    journal_name: str
    write_start_date: Optional[str] = None
    profile_start_date: Optional[str] = None
    currency: str  # USD | INR
    payment_status: str  # pending | partial | paid

    # Optional manuscript fields
    create_manuscript: bool = False
    manuscript_title: Optional[str] = None
    manuscript_journal_name: Optional[str] = None

    # Optional payment fields
    create_payment: bool = False
    payment_amount: Optional[float] = None
    payment_phase: Optional[int] = None
    payment_date: Optional[str] = None
    payment_received_account: Optional[str] = None
