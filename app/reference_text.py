"""Conservative cleanup of model-added paragraph wrappers, never of user answers."""
import json
import re


def paragraph_wrapper(line):
    core=line.strip()
    if len(core)>4 and core.startswith('\\"') and core.endswith('\\"'):
        return 2
    if len(core)>2 and core.startswith('"') and core.endswith('"'):
        return 1
    # An indent INSIDE repeated smart quotes indicates a wrapped paragraph.
    # Ordinary Chinese quotations, including entire quoted paragraphs, stay intact.
    if re.match(r'^“(?:　{2}| {2})\S',core) and core.endswith('”'):
        return 1
    return 0


def normalize_reference_text(value):
    if not isinstance(value,str):return value
    text=value
    for _ in range(8):
        previous=text
        # A complete second JSON string containing paragraphs is unambiguous.
        encoded=text.strip()
        for _layer in range(3):
            try:decoded=json.loads(encoded)
            except (ValueError,TypeError):break
            if not isinstance(decoded,str):break
            if '\n' in decoded:
                text=decoded
                break
            encoded=decoded
        lines=text.splitlines(keepends=True)
        body=[i for i,line in enumerate(lines) if line.strip()]
        if body and not paragraph_wrapper(lines[body[0]]):
            title=lines[body[0]].strip()
            if len(title)<=40 and not title.endswith(('。','！','？','；','.','!','?',';')):body=body[1:]
        if len(body)>=2 and all(paragraph_wrapper(lines[i]) for i in body):
            for i in body:
                line=lines[i];core=line.strip();width=paragraph_wrapper(line)
                leading=line[:len(line)-len(line.lstrip())]
                trailing=line[len(line.rstrip()):]
                inner=core[width:-width]
                # Decode only a genuine JSON-encoded line; never unicode_escape
                # or globally remove quotes, backslashes or legitimate punctuation.
                if width==1 and core.startswith('"'):
                    try:decoded=json.loads(core)
                    except ValueError:decoded=None
                    if isinstance(decoded,str):inner=decoded
                lines[i]=leading+inner+trailing
            text=''.join(lines)
        if text==previous:break
    return text
