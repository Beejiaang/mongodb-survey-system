"""Additional phase-two collections; existing survey/response storage is retained."""
from copy import deepcopy
from survey_schema import QUESTION_SCHEMA, SOURCE_SCHEMA

OID = {"bsonType": "objectId"}
MAYBE_OID = {"bsonType": ["objectId", "null"]}
DATE = {"bsonType": "date"}
TEXT = {"bsonType": "string", "minLength": 1, "maxLength": 200, "pattern": r"\S"}
NOTE = {"bsonType": "string", "maxLength": 500}


def object_schema(properties):
    return {"bsonType": "object", "required": list(properties),
            "additionalProperties": False, "properties": deepcopy(properties)}


ITEM_VALIDATOR = {"$jsonSchema": object_schema({
    "_id": OID, "owner_id": OID, "name": TEXT,
    "current_version_id": MAYBE_OID,
    "current_version_number": {"bsonType": ["int", "long"], "minimum": 0},
    "next_version": {"bsonType": ["int", "long"], "minimum": 0},
    "shared_with": {"bsonType": "array", "uniqueItems": True, "items": OID},
    "forked_from_version_id": MAYBE_OID, "created_at": DATE, "updated_at": DATE,
})}
CONTENT_SCHEMA = deepcopy(QUESTION_SCHEMA)
for variant in CONTENT_SCHEMA["oneOf"]:
    variant["properties"].pop("source")
    variant["properties"]["jump_rules"] = {"bsonType": "array", "maxItems": 0}
VERSION_VALIDATOR = {"$jsonSchema": object_schema({
    "_id": OID, "question_id": OID, "number": {"bsonType": ["int", "long"], "minimum": 1},
    "content": CONTENT_SCHEMA, "parent_version_id": MAYBE_OID,
    "previous_version_id": MAYBE_OID, "restored_from_version_id": MAYBE_OID,
    "change_note": NOTE, "created_by": OID, "created_at": DATE,
})}
BANK_VALIDATOR = {"$jsonSchema": object_schema({
    "_id": OID, "owner_id": OID, "name": TEXT, "description": NOTE,
    "entries": {"bsonType": "array", "uniqueItems": True, "items": SOURCE_SCHEMA},
    "created_at": DATE, "updated_at": DATE,
})}
LIBRARY_VALIDATORS = {"question_items": ITEM_VALIDATOR, "question_versions": VERSION_VALIDATOR,
                      "question_banks": BANK_VALIDATOR}
