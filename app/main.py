from fastapi import FastAPI

app = FastAPI(title="FileAway API")

@app.get("/")
def home():
    return {"message": "FileAway API is running"}