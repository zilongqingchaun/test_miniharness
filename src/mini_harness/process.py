"""Bounded subprocess output and process-tree cleanup on timeout."""
import os
import signal
import subprocess
import threading


def run_command(command, cwd, timeout, env=None, limit=30000):
    process = subprocess.Popen(command, shell=isinstance(command, str), cwd=cwd, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=os.name != 'nt',
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
    output = bytearray()
    clipped = False

    def drain():
        nonlocal clipped
        with process.stdout:
            while chunk := process.stdout.read(8192):
                remaining = max(0, limit - len(output))
                output.extend(chunk[:remaining])
                clipped |= len(chunk) > remaining

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout)
    except BaseException as error:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           capture_output=True, timeout=5)
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        if isinstance(error, subprocess.TimeoutExpired):
            raise TimeoutError(f'Command exceeded {timeout:.1f}s') from error
        raise
    finally:
        reader.join(timeout=1)
    text = output.decode('utf-8', errors='replace')
    if clipped:
        text += f'\n[truncated at {limit} bytes]'
    return process.returncode, text
