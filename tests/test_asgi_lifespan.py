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

    async def test_lifespan_timeout(self):
        async def slow_app(scope, receive, send):
            if scope['type'] == 'lifespan':
                await receive()
                # Simulate slow startup by not sending complete
                await asyncio.sleep(0.5)

        adapter = ASGIAdapter(slow_app)
        
        start_time = asyncio.get_event_loop().time()
        # Set a short timeout
        await adapter.startup(timeout=0.1)
        end_time = asyncio.get_event_loop().time()
        
        # Should return after ~0.1s due to timeout
        self.assertLess(end_time - start_time, 0.4)
        self.assertGreaterEqual(end_time - start_time, 0.1)

if __name__ == "__main__":
    unittest.main()
