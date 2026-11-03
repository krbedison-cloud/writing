DEFAULT_MAX_TOKENS = 32768
MAX_OUTPUT_TOKENS = 131072

def next_token_limit(current):
    target=min(MAX_OUTPUT_TOKENS,max(DEFAULT_MAX_TOKENS,current*2))
    return next((value for value in (32768,65536,131072) if value>current and value>=target),None)
