"""Size and count limits: the single source of the numbers (spec §71.4, §64.4)."""

MAX_EVENT_BYTES = 256 * 1024
MAX_INLINE_PAYLOAD_BYTES = 64 * 1024
MAX_ATTRIBUTES = 64
MAX_ATTRIBUTE_KEY_LENGTH = 128
MAX_ATTRIBUTE_STRING_LENGTH = 4096
MAX_ATTRIBUTE_LIST_ITEMS = 64
MAX_TAGS = 16
MAX_TAG_LENGTH = 64
MAX_PAYLOAD_REF_LENGTH = 512
MAX_NAME_LENGTH = 256

# JSON numbers beyond 2**53 lose precision in JavaScript (the TypeScript SDK and the web app),
# so integers on the wire stay inside the safe range.
MAX_SAFE_INTEGER = 2**53 - 1
