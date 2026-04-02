from fastapi import WebSocket
import time


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, dict] = {}

    async def connect(self, websocket: WebSocket, provider_id: int):
        await websocket.accept()
        self.active_connections[provider_id] = {
            "socket": websocket,
            "start_time": time.time(),
        }

    def disconnect(self, provider_id: int) -> float:
        """Supprime le provider et retourne la durée de connexion en secondes"""
        conn_data = self.active_connections.pop(provider_id, None)
        if conn_data:
            return time.time() - conn_data["start_time"]
        return 0.0

    def is_provider_online(self, provider_id: int) -> bool:
        return provider_id in self.active_connections


manager = ConnectionManager()
