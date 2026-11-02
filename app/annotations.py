import hashlib
import re

WHITESPACE = re.compile(r'[\s\ufeff]')

def canonical_text(text):
    return WHITESPACE.sub('', text)

def material_signature(text):
    return hashlib.sha256(canonical_text(text).encode()).hexdigest()
