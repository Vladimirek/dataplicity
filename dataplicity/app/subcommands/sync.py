from dataplicity.app.subcommand import SubCommand
from dataplicity.app import comms


class Sync(SubCommand):
    """Sync with server"""
    help = """Sync with server"""

    def run(self):
        comms.Comms().sync()
