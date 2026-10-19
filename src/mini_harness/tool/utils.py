from pathlib import Path
import hashlib
def digest(p: Path) -> str:
    h = hashlib.md5()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()

def render_lines(val_path: Path, offset: int|None, limit: int|None, cfg = CONFIG) -> tuple[str, bool]:
    try:
        content = val_path.read_text(encoding = 'utf-8')
    except UnicodeDecodeError:
        size = val_path.stat().st_size
        return f'[Binary or non-UTF-8 file, {size} bytes. Content not displayed]', True
    lines = _split_lines(content)
    total = len(lines)
    if total == 0:
        return f'[The file is empty (0 lines)]', True

    start = (offset if offset else 1) - 1
    end = start + limit if limit else total
    window = lines[start: end]
    if not window:
        raise ValueError(f'[offset error]: offset {offset} exceeds the file length ({total} lines), use an offset between 1 and {total}')

    out, used = [], 0
    for lineno, line in enumerate(window, start + 1): 
        render = f'{lineno:>6}\t{line}'
        out.append(render)
        used += len(render)
        if used >= cfg.read_limit:
            break

    last = start + len(out)
    body = '\n'.join(out)
    reach_end = (last >= total)
    if not reach_end:
        body += f'\n\n{MORE}{start+1}-{last} of {total}, use offset {last + 1} to continue]'

    return body, reach_end


def _split_lines(content: str) -> list[str]:
    if not content:
        return []

    content = content.replace('\r\n', '\n')
    content_split = content.split('\n')
    if content_split and not content_split[-1]:
        content_split.pop()
    return content_split