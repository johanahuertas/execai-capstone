from fastapi import FastAPI

app = FastAPI(title="ExecAI Backend")

@app.get("/health")
def health_check():
    return {"status": "ok"}

