from fastapi import FastAPI, HTTPException, Response, UploadFile, File
from pydantic import BaseModel
from contextlib import asynccontextmanager
import bot
import os
import shutil
import tempfile
import asyncio

# Global bot instance
import processor

wechat_bot = bot.WeChatHelperBot()
command_processor = processor.CommandProcessor(wechat_bot)
listener_task = None

async def background_listener():
    """Polls for new messages and processes them."""
    print("Background listener started.")
    # Use a set to track processed message IDs or content to avoid dupes
    # Since we added 'id' extraction, we can try to use it.
    processed_ids = set()
    processed_hashes = set()
    
    while True:
        try:
            if wechat_bot.is_logged_in:
                messages = await wechat_bot.get_latest_messages(limit=5)
                
                # Process oldest first
                for msg in reversed(messages):
                    content = msg.get('text', '').strip()
                    msg_id = msg.get('id')
                    
                    # Deduplication logic
                    # If we have an ID, use it. If not, use content hash as fallback (risky for identical repeated msgs)
                    unique_key = msg_id if msg_id else content
                    
                    if unique_key and unique_key not in processed_ids:
                        print(f"New message processing: {content[:20]}...")
                        processed_ids.add(unique_key)
                        # clean up old ids
                        if len(processed_ids) > 200:
                            processed_ids.pop()
                            
                        reply = await command_processor.process(msg)
                        if reply:
                            print(f"Replying: {reply}")
                            await wechat_bot.send_text(reply)
            
        except Exception as e:
            print(f"Listener error: {e}")
        
        await asyncio.sleep(2) # Poll every 2 seconds

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    # Note: headless=False might be needed for the first manual login if no state exists.
    # But usually we run headless=True and use the QR code endpoint.
    await wechat_bot.start(headless=True)
    
    # Start background listener
    global listener_task
    listener_task = asyncio.create_task(background_listener())
    
    yield
    
    # Shutdown
    if listener_task:
        listener_task.cancel()
        try:
            await listener_task
        except asyncio.CancelledError:
            pass
            
    await wechat_bot.save_session() # Save on exit
    await wechat_bot.stop()

app = FastAPI(lifespan=lifespan, title="WeChat FileHelper API")

class Message(BaseModel):
    content: str
    
@app.get("/")
async def root():
    """Check service status and login state."""
    is_logged_in = await wechat_bot.check_login_status()
    return {
        "service": "WeChat FileHelper IPC",
        "logged_in": is_logged_in,
        "instructions": "Go to /qr to see the login code. Send POST to /send to send text. POST /upload to send file."
    }

@app.get("/qr")
async def get_qr():
    """Get the login QR code as an image."""
    try:
        # If already logged in, inform the user
        if await wechat_bot.check_login_status():
             return Response(content="Already logged in. You can now use /send.", media_type="text/plain")
        
        png_bytes = await wechat_bot.get_login_qr()
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send")
async def send_message(msg: Message):
    """Send a message to yourself (File Transfer Assistant)."""
    # Double check login status before acting
    is_logged_in = await wechat_bot.check_login_status()
    if not is_logged_in:
        raise HTTPException(
            status_code=401, 
            detail="Session not active. Please open the browser window or scan the QR code at /qr"
        )
    
    success = await wechat_bot.send_text(msg.content)
    if success:
        return {"status": "sent", "content": msg.content}
    else:
        raise HTTPException(
            status_code=500, 
            detail="Failed to send message. Browser interaction failed."
        )

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to yourself."""
    is_logged_in = await wechat_bot.check_login_status()
    if not is_logged_in:
        raise HTTPException(status_code=401, detail="Session not active.")

    # Save uploaded file to temp
    suffix = os.path.splitext(file.filename)[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    
    try:
        success = await wechat_bot.send_file(tmp_path)
        if success:
            return {"status": "sent", "filename": file.filename}
        else:
            raise HTTPException(status_code=500, detail="Failed to send file via browser.")
    finally:
        # Cleanup temp file
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

@app.get("/messages")
async def get_messages(limit: int = 10):
    """Get recent messages from the chat window."""
    messages = await wechat_bot.get_latest_messages(limit)
    return {"messages": messages}

@app.post("/save_session")
async def trigger_save_session():
    """Force save session state."""
    success = await wechat_bot.save_session()
    if success:
        return {"status": "saved"}
    else:
        raise HTTPException(status_code=500, detail="Failed to save session")

@app.get("/debug_html")
async def debug_html():
    """Dump current page HTML for debugging selectors."""
    source = await wechat_bot.get_page_source()
    return Response(content=source, media_type="text/html")

if __name__ == "__main__":
    import uvicorn
    # Run on 0.0.0.0 to be accessible
    uvicorn.run(app, host="0.0.0.0", port=8000)
