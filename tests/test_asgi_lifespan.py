import unittest
import asyncio
from fcgisgi.asyncio_server import Server


class TestASGILifespan(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_flow(self):
        startup_called = False
        shutdown_called = False

        async def app(scope, receive, send):
            nonlocal startup_called, shutdown_called
            if scope["type"] == "lifespan":
                while True:
                    message = await receive()
                    if message["type"] == "lifespan.startup":
                        startup_called = True
                        await send({"type": "lifespan.startup.complete"})
                    elif message["type"] == "lifespan.shutdown":
                        shutdown_called = True
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        server = Server(app, startup_timeout=0.1, shutdown_timeout=0.1)

        # Test startup via run (partially) or internal methods
        # To keep it simple, we test the Server's lifespan management
        server.loop = asyncio.get_running_loop()
        server._stop_event = asyncio.Event()

        # Let's simulate a quick run/stop.
        server_task = asyncio.create_task(server.run(("127.0.0.1", 0)))
        await asyncio.sleep(0.1)
        self.assertTrue(startup_called)

        server.stop()
        await server_task
        self.assertTrue(shutdown_called)

    async def test_lifespan_timeout(self):
        async def slow_app(scope, receive, send):
            if scope["type"] == "lifespan":
                await receive()
                await asyncio.sleep(0.5)

        server = Server(slow_app, startup_timeout=0.1, shutdown_timeout=0.1)
        server_task = asyncio.create_task(server.run(("127.0.0.1", 0)))

        await asyncio.sleep(0.2)

        self.assertTrue(server.startup_complete)
        server.stop()
        await server_task


if __name__ == "__main__":
    unittest.main()
