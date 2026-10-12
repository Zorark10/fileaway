from app.database import Base, engine
from app.models import File

Base.metadata.create_all(bind=engine)
print("Tables Created succesfully")