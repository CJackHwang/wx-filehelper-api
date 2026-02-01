from playwright.async_api import async_playwright, Page, Browser
from playwright.async_api import async_playwright, Page, Browser
import asyncio
import os
from PIL import Image

class WeChatHelperBot:
    def __init__(self):
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.playwright = None
        self.is_logged_in = False
        self.lock = asyncio.Lock()

    async def start(self, headless=True, state_path="state.json"):
        """Starts the browser and navigates to the file helper page."""
        print(f"Starting browser (Headless: {headless})...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        
        # Load state if exists
        if os.path.exists(state_path):
            print(f"Loading session from {state_path}")
            context = await self.browser.new_context(storage_state=state_path)
        else:
            print("No previous session found, starting fresh.")
            context = await self.browser.new_context()

        self.page = await context.new_page()
        # Set a clear viewport
        await self.page.set_viewport_size({"width": 1280, "height": 800})
        
        # RESOURCE OPTIMIZATION: Block unnecessary resources
        await self.page.route("**/*", lambda route: route.abort() 
            if route.request.resource_type in ["image", "stylesheet", "font", "media"] 
            and "qr" not in route.request.url # Keep QR codes unblocked if they are images
            else route.continue_()
        )
        
        print("Navigating to https://filehelper.weixin.qq.com/ ...")
        await self.page.goto("https://filehelper.weixin.qq.com/")
        print("Page loaded.")

    async def save_session(self, path="state.json"):
        """Saves the current browser state (cookies, local storage) to a file."""
        if self.page:
            await self.page.context.storage_state(path=path)
            print(f"Session saved to {path}")
            return True
        return False

    async def get_login_qr(self) -> bytes:
        """Returns the screenshot of the page (focused on QR code if possible)."""
        if not self.page:
            raise Exception("Browser not initialized")
        
        # Give it a moment to render the QR code
        await asyncio.sleep(2)
        
        # In a real scenario, we might want to crop to the QR code, 
        # but full page is safer for the first version to ensure the user sees everything.
        return await self.page.screenshot(full_page=False)

    async def save_screenshot(self, path: str) -> bool:
        """Saves a screenshot to the specified path."""
        if not self.page:
            return False
        try:
            await self.page.screenshot(path=path)
            return True
        except Exception as e:
            print(f"Screenshot failed: {e}")
            return False

    async def check_login_status(self) -> bool:
        """Checks if the user has successfully logged in."""
        if not self.page:
            return False
            
        # We look for the chat input area. 
        # In modern web apps, the chat input is almost always a div with contenteditable="true"
        try:
            input_locator = self.page.locator("div[contenteditable='true']")
            if await input_locator.count() > 0 and await input_locator.is_visible():
                self.is_logged_in = True
                return True
        except Exception:
            pass
        
        self.is_logged_in = False
        return False

    async def send_text(self, message: str) -> bool:
        """Sends a text message."""
        if not self.is_logged_in:
            if not await self.check_login_status():
                return False
        
        async with self.lock:
            try:
                # 1. Click the input box
                box = self.page.locator("div[contenteditable='true']")
                await box.click()
                
                # 2. Type the message
                # We use playwright's type to simulate keystrokes, which is more natural
                await self.page.keyboard.type(message)
                
                # 3. Press Enter to send
                await self.page.keyboard.press("Enter")
                
                return True
            except Exception as e:
                print(f"Error sending message: {e}")
                return False

    async def send_file(self, file_path: str) -> bool:
        """Sends a file."""
        if not self.is_logged_in:
            if not await self.check_login_status():
                return False

        if not os.path.exists(file_path):
            print(f"File not found: {file_path}")
            return False

        async with self.lock:
            try:
                # WeChat Web usually has a hidden file input for uploads.
                # We can try to assume there is an input[type='file'] on the page.
                # If not, we might need to click the '+' button first.
                
                # Strategy 1: Direct input setting (Most robust if input exists)
                # The file input might be dynamic, so we'll try to find it.
                # Common in web apps: input[type='file']
                
                # We need to make sure the file input is present. 
                # Sometimes it only appears after clicking the 'Clip' icon.
                # Let's try to click the 'Toolbar's file icon if we can find it.
                # Standard WeChat Web toolbar often has a button with title="Send File" or similar class
                
                # Fallback: Just try to set input files on *any* file input.
                async with self.page.expect_file_chooser(timeout=2000) as fc_info:
                    # Try clicking the "Folder" icon usually associated with file transfer
                    # detailed selector might be needed here, or we simulate a drag and drop?
                    # Drag and drop is often easier for "File Transfer" pages.
                    pass
                
                # Actually, forcing an input[type='file'] is cleaner.
                # Let's try locating the input.
                file_input = self.page.locator("input[type='file']")
                if await file_input.count() > 0:
                    await file_input.set_input_files(file_path)
                    # Often need to confirm dispatch
                    # Wait for a send button if it pops up, or it sends automatically.
                    # In FileTransfer helper, it often sends immediately or puts it in the box.
                else:
                    # If no input, maybe we need to paste the file? (Clipboard API)
                    # Or use drag and drop buffer
                    # Drag and drop buffer:
                    # Create a drag data transfer
                    # This is complex. Let's stick to 'input' first.
                    print("No file input found. Trying to find upload button.")
                    return False

                # If the file is put in the chat box but not sent (e.g. requires another Enter)
                # We press enter just in case
                await asyncio.sleep(1)
                await self.page.keyboard.press("Enter")
                return True

            except Exception as e:
                print(f"Error sending file: {e}")
                # Fallback: Try to use the 'chooser' pattern if clicking the button works
                try: 
                    # Attempt to find common upload button selectors
                    # This is speculative without DOM access
                    # await self.page.click("api-selector-for-upload-button") 
                    pass
                except:
                    pass
                return False

    async def get_latest_messages(self, limit=10):
        """Scrapes the last few messages from the DOM."""
        if not self.page:
            return []
        
        # This requires knowing the DOM structure.
        # General Strategy: Find all text nodes in the chat container.
        # We will try to find a generic container.
        # Assuming typical chat app structure:
        # div.message
        #   div.content
        
        try:
            # Execute JS to extract text from what look like message bubbles
            # We look for elements that might be messages.
            # In WeChat, they are often js_message_bubble
            
            messages = await self.page.evaluate("""() => {
                // Heuristic: Find the main scrolling container
                const messageElements = Array.from(document.querySelectorAll('.message, .js_message_bubble, .bubble'));
                return messageElements.slice(-""" + str(limit) + """).map(el => {
                    return {
                        text: el.innerText,
                        type: el.className,
                        id: el.getAttribute('id') || el.getAttribute('data-id') || el.getAttribute('data-cmid'),
                        dataset: Object.assign({}, el.dataset), # Copy dataset
                        html: el.outerHTML.substring(0, 200) # Debug snippet
                    };
                });
            }""")
            return messages
        except Exception as e:
            print(f"Error getting messages: {e}")
            return []

    async def get_page_source(self) -> str:
        """Debug method to dump HTML if selectors change."""
        if self.page:
            return await self.page.content()
        return ""

    async def stop(self):
        print("Closing browser...")
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
