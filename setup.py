import os
import sys

def check_models():
    print("Checking vision models...")
    models_dir = os.path.join("backend", "models")
    os.makedirs(models_dir, exist_ok=True)
    
    quality_path = os.path.join(models_dir, "egg_quality_efficientnetb2.pth")
    fertility_path = os.path.join(models_dir, "egg_fertility_efficientnetb2.pth")
    
    missing = []
    if not os.path.exists(quality_path):
        missing.append(quality_path)
    if not os.path.exists(fertility_path):
        missing.append(fertility_path)
        
    if missing:
        print(f"WARNING: The following custom model files are missing:\n" + "\n".join(missing))
        print("Please place them in the specified locations before running the backend.")
        print("The agent will continue with fallback logic if they are missing.")
    else:
        print("Vision models found.")

def init_db():
    print("Initializing SQLite database...")
    from backend.db.database import Base, engine, SessionLocal
    from backend.db.models import Lot, Alert, Feedback, Stock, PartnerOrder, DispatchLog, QualityIncident
    from datetime import datetime, timedelta
    
    # Create all tables (including legacy ones)
    Base.metadata.create_all(bind=engine)
    
    # Create explicitely the new tables checkfirst
    Stock.__table__.create(bind=engine, checkfirst=True)
    PartnerOrder.__table__.create(bind=engine, checkfirst=True)
    DispatchLog.__table__.create(bind=engine, checkfirst=True)
    QualityIncident.__table__.create(bind=engine, checkfirst=True)
    print("New supply chain tables created successfully")

    # Seed PartnerOrders if empty
    session = SessionLocal()
    if session.query(PartnerOrder).count() == 0:
        now = datetime.utcnow()
        orders = []
        if orders:
            session.bulk_save_objects(orders)
            session.commit()
            print("Partner orders seeded successfully")
        else:
            print("No template orders to seed.")
    else:
        print("Partner orders already exist — skipping seed")
    session.close()
    
    print("Database initialized.")

def init_chroma():
    print("Initializing ChromaDB collections...")
    from backend.tools.rag_tools import _ingest_initial_data
    _ingest_initial_data()
    print("ChromaDB initialized.")
    
if __name__ == "__main__":
    check_models()
    try:
        init_db()
        init_chroma()
    except Exception as e:
        print(f"Error initializing DB/ChromaDB: {e}")
        print("Make sure you have installed the requirements using `pip install -r backend/requirements.txt`")
        
    print("\nSetup complete. You can now run the backend using:")
    print("uvicorn backend.main:app --reload")
