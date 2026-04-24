from pymongo import MongoClient
from app.config import MONGO_URI, DB_NAME

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

users_collection = db["users"]
tokens_collection = db["tokens"]
clients_collection = db["clients"]
orders_collection = db["orders"]
manuscripts_collection = db["manuscripts"]
payments_collection = db["payments"]
otps_collection = db["otps"]

# --- INDEXES FOR PERFORMANCE ---
users_collection.create_index("email", unique=True)
users_collection.create_index("full_name")
clients_collection.create_index("client_id", unique=True)
clients_collection.create_index("client_handler")
orders_collection.create_index("client_id")
orders_collection.create_index("order_id", unique=True)
orders_collection.create_index("reference_id")
payments_collection.create_index([("order_id", 1), ("phase", 1)])
payments_collection.create_index("client_id")
