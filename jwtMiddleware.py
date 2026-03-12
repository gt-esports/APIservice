import time
import jwt
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse

app = FastAPI()

DevSecret = "herro123"
ALGORITHM = "HS256"

@app.middleware("http")
async def jwt_middleware_header(request: Request, call_next):
    auth_header = request.headers.get("Authorization")

    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            status_code=401,
            content={"error": "Missing or invalid Authorization header"}
        )

    token = auth_header.split(" ")[-1]

    try:
        user_data = jwt.decode(
            token, 
            DevSecret, 
            algorithms=[ALGORITHM], 
            options={"require": ["exp", "sub"]}
        )
    except jwt.MissingRequiredClaimError as e:
        return JSONResponse(
            status_code=401,
            content={"error": str(e)}
        )
    except jwt.ExpiredSignatureError:
        return JSONResponse(
            status_code=401,
            content={"error": "Expired Authorization Token Signature"}
        )
    except jwt.InvalidSignatureError:
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid Authorization Token Signature"}
        )
    except jwt.DecodeError:
        return JSONResponse(
            status_code=401,
            content={"error": "Error Decoding Authorization Token"}
        )
    except jwt.InvalidTokenError: # The catch-all 
        return JSONResponse(
            status_code=401,
            content={"error": "Invalid Authorization Token"}
        )
    
    request.state.user = user_data
    response = await call_next(request)
    
    return response