import os
import tempfile

class CommandProcessor:
    """
    Handles business logic for incoming commands.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.commands = {
            "ping": self.cmd_ping,
            "help": self.cmd_help,
            "echo": self.cmd_echo,
            "screenshot": self.cmd_screenshot
        }

    async def process(self, msg: dict) -> str | None:
        """
        Process a message dictionary and return a text reply, or None.
        Side effects (sending files) can be done directly via self.bot.
        """
        text = msg.get('text', '')
        if not text:
            return None
            
        parts = text.strip().split()
        if not parts:
            return None
            
        cmd = parts[0].lower()
        args = parts[1:]
        
        if cmd in self.commands:
            return await self.commands[cmd](args, text)
        
        return None

    async def cmd_ping(self, args, full_text):
        return "pong"

    async def cmd_help(self, args, full_text):
        return f"Available commands: {', '.join(self.commands.keys())}"

    async def cmd_echo(self, args, full_text):
        return full_text[5:] if len(full_text) > 5 else ""

    async def cmd_screenshot(self, args, full_text):
        """Taking a screenshot and sending it back."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
            
        try:
            if await self.bot.save_screenshot(tmp_path):
                await self.bot.send_file(tmp_path)
                return "Screenshot sent."
            else:
                return "Failed to take screenshot."
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
