import unicodedata


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split()).strip(". !")


DELIVERED_NAMES = {
    "delivered",
    "delivered successfully",
    "delivery successful",
    "order delivered",
    "da giao",
    "da giao thanh cong",
    "giao thanh cong",
    "da giao hang",
    "giao hang thanh cong",
    "da giao hang thanh cong",
    "don hang da giao thanh cong",
    "da ky nhan",
}


def is_delivered_status(*values: str) -> bool:
    return any(normalized(value) in DELIVERED_NAMES for value in values)
