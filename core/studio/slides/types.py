"""Slide type and element type constants for the Forge slides pipeline."""

# 12 supported slide types
SLIDE_TYPES = {
    "title",
    "content",
    "two_column",
    "comparison",
    "timeline",
    "chart",
    "image_text",
    "image_full",
    "quote",
    "code",
    "team",
    "stat",
}

# 9 supported element types
ELEMENT_TYPES = {
    "title",
    "subtitle",
    "body",
    "bullet_list",
    "image",
    "chart",
    "code",
    "quote",
    "stat_callout",
}

# Slide-type-to-element mapping
SLIDE_TYPE_ELEMENTS = {
    "title":       ["title", "subtitle"],
    "content":     ["title", "body", "bullet_list"],
    "two_column":  ["title", "body", "bullet_list"],
    "comparison":  ["title", "body", "bullet_list"],
    "timeline":    ["title", "body", "bullet_list"],
    "chart":       ["title", "chart", "body"],
    "image_text":  ["title", "image", "body"],
    "image_full":  ["title", "image", "body"],
    "quote":       ["quote", "body"],
    "code":        ["title", "code", "body"],
    "team":        ["title", "body", "bullet_list"],
    "stat":        ["title", "stat_callout", "body"],
}

# Narrative arc pattern — varied with no consecutive repeats
NARRATIVE_ARC = [
    "title",
    "content",
    "stat",
    "two_column",
    "timeline",
    "image_text",
    "chart",
    "quote",
    "content",
    "title",
]


def is_valid_slide_type(slide_type: str) -> bool:
    return slide_type in SLIDE_TYPES


def is_valid_element_type(element_type: str) -> bool:
    return element_type in ELEMENT_TYPES


def get_elements_for_slide_type(slide_type: str) -> list[str]:
    return SLIDE_TYPE_ELEMENTS.get(slide_type, ["title", "body"])
