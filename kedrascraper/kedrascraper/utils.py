import os

class Utils:
    
    @staticmethod
    def env_bool(name, default):
        return os.environ.get(name, str(default)).strip().lower() == "true"
