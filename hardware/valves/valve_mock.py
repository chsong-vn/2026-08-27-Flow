class MockValve:
    def __init__(self, port):
        self.port = port
        self.position = "UNKNOWN"
        
    def connect(self):
        print(f"   [MockValve] Connected on virtual port {self.port}")
        
    def set_position(self, pos):
        self.position = pos
        print(f"   [MockValve] {self.port} moved to >> {pos}")