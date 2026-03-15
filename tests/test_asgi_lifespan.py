import unittest
import asyncio
import struct
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

    async def test_reject_requests_during_lifespan(self):
        async def app(scope, receive, send):
            if scope['type'] == 'lifespan':
                while True:
                    msg = await receive()
                    if msg['type'] == 'lifespan.startup':
                        await send({'type': 'lifespan.startup.complete'})
                    elif msg['type'] == 'lifespan.shutdown':
                        await send({'type': 'lifespan.shutdown.complete'})
                        return

        output = bytearray()
        def send_func(data):
            output.extend(data)

        adapter = ASGIAdapter(app, send_func)
        
        # 1. Try request before startup
        from fcgisgi.sansio import FCGI_BEGIN_REQUEST, FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST_BODY_FORMAT, FCGI_OVERLOADED
        content = struct.pack(FCGI_BEGIN_REQUEST_BODY_FORMAT, 1, 1)
        header = struct.pack(FCGI_HEADER_FORMAT, FCGI_VERSION_1, FCGI_BEGIN_REQUEST, 1, len(content), 0)
        adapter.handle_data(header + content)
        
        self.assertIn(struct.pack("!LB3x", 0, FCGI_OVERLOADED), output)
        output.clear()

        # 2. Startup
        await adapter.startup()
        
        # 3. Try request after startup (should NOT be rejected)
        adapter.handle_data(header + content)
        self.assertNotIn(struct.pack("!LB3x", 0, FCGI_OVERLOADED), output)
        output.clear()

        # 4. Shutdown
        await adapter.shutdown()
        
        # 5. Try request after shutdown
        adapter.handle_data(header + content)
        self.assertIn(struct.pack("!LB3x", 0, FCGI_OVERLOADED), output)

if __name__ == "__main__":
    unittest.main()
