import unittest
import asyncio
from fcgisgi.asgi_adapter import ASGIAdapter

class TestASGILifespan(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_flow(self):
        startup_called = False
        shutdown_called = False

        async def app(scope, receive, send):
            nonlocal startup_called, shutdown_called
            if scope['type'] == 'lifespan':
                while True:
                    message = await receive()
                    if message['type'] == 'lifespan.startup':
                        startup_called = True
                        await send({'type': 'lifespan.startup.complete'})
                    elif message['type'] == 'lifespan.shutdown':
                        shutdown_called = True
                        await send({'type': 'lifespan.shutdown.complete'})
                        return

        adapter = ASGIAdapter(app)
        
        # Test startup
        await adapter.startup()
        self.assertTrue(startup_called)
        
        # Test shutdown
        await adapter.shutdown()
        self.assertTrue(shutdown_called)

if __name__ == "__main__":
    unittest.main()
