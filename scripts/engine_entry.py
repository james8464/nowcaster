from multiprocessing import freeze_support

from src.cli import app

if __name__ == "__main__":
    freeze_support()
    app()
