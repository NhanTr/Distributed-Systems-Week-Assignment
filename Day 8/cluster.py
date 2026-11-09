"""Own the five spawned replica processes and their TCP listening sockets."""
import multiprocessing
from pathlib import Path
import socket
import tempfile
import time
from gossip_quorum import rpc, serve_replica


class Cluster:
    def __init__(self, gossip_enabled=True, interval=2.0, log_dir=None):
        self.gossip_enabled, self.interval = gossip_enabled, interval
        if interval <= 0:
            raise ValueError('interval must be positive')
        self.temp = tempfile.TemporaryDirectory() if log_dir is None else None
        self.log_dir = Path(self.temp.name if self.temp else log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.peers, self.processes = {}, {}

    def start(self):
        context = multiprocessing.get_context('spawn')
        listeners = {}
        try:
            for i in range(1, 6):
                name = f'S{i}'
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listeners[name] = listener
                listener.bind(('127.0.0.1', 0))
                listener.listen(32)
                self.peers[name] = listener.getsockname()
            for name, listener in listeners.items():
                process = context.Process(target=serve_replica, name=name, args=(
                    listener, name, self.peers, str(self.log_dir / f'{name}.log'),
                    self.gossip_enabled, self.interval))
                process.start()
                self.processes[name] = process
            # Close parent copies before allowing tests to stop nodes.
            for listener in listeners.values():
                listener.close()
            deadline = time.monotonic() + 10
            for name, address in self.peers.items():
                while True:
                    if not self.processes[name].is_alive():
                        raise RuntimeError(f'{name} exited during startup')
                    try:
                        rpc(address, {'op': 'ping'}, timeout=.2)
                        break
                    except (OSError, ValueError):
                        if time.monotonic() > deadline:
                            raise RuntimeError(f'{name} startup timed out')
                        time.sleep(.05)
            return self
        except BaseException:
            for listener in listeners.values():
                listener.close()
            self.close()
            raise

    def stop(self, name):
        process = self.processes[name]
        if process.is_alive():
            process.terminate()
        process.join(timeout=3)
        if process.is_alive():
            process.kill()
            process.join(timeout=3)

    def close(self):
        for name in self.processes:
            self.stop(name)
        if self.temp:
            self.temp.cleanup()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
