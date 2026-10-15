"""验证用户名规则、用户文档及密码验证使用一致的数据约定。"""

from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock

from bson import ObjectId
from werkzeug.security import check_password_hash

from users import (
    build_user_document,
    find_user_by_username,
    normalize_username,
    verify_user_password,
)


class UsernameTests(unittest.TestCase):
    def test_strips_outer_whitespace_and_lowercases_ascii_letters(self):
        self.assertEqual(normalize_username(" \tAlice_123\r\n"), "alice_123")

    def test_accepts_both_length_boundaries_after_stripping(self):
        for username in ("Ab_", "A" * 30):
            with self.subTest(username=username):
                self.assertEqual(
                    normalize_username("  " + username + "  "), username.lower()
                )

    def test_rejects_short_and_long_usernames(self):
        for username in ("", "   ", "a", "ab", "a" * 31):
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    normalize_username(username)

    def test_rejects_punctuation_internal_whitespace_and_unicode(self):
        for username in (
            "ali.ce",
            "ali-ce",
            "alice@",
            "ali ce",
            "ali\tce",
            "ali\nce",
            "用户甲",
            "élice",
            "Ａlice",
            "abc١",
            "Kelvin",  # Unicode Kelvin sign lowercases to ASCII k.
            "İce",
        ):
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    normalize_username(username)

    def test_rejects_non_string_usernames(self):
        for username in (None, 123, True, b"alice", ["alice"], {"name": "alice"}):
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    normalize_username(username)


class UserDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before_creation = datetime.now(timezone.utc)
        cls.user = build_user_document("  Alice_123  ", "Example password!")
        cls.after_creation = datetime.now(timezone.utc)

    def test_document_contains_exactly_the_four_agreed_fields(self):
        self.assertEqual(
            set(self.user), {"_id", "username", "password_hash", "created_at"}
        )
        self.assertIsInstance(self.user["_id"], ObjectId)

    def test_document_stores_normalized_username(self):
        self.assertEqual(self.user["username"], "alice_123")

    def test_created_at_is_current_timezone_aware_utc_datetime(self):
        created_at = self.user["created_at"]
        self.assertIsInstance(created_at, datetime)
        self.assertIsNotNone(created_at.tzinfo)
        self.assertEqual(created_at.utcoffset(), timedelta(0))
        self.assertLessEqual(self.before_creation, created_at)
        self.assertLessEqual(created_at, self.after_creation)

    def test_password_is_stored_as_a_working_scrypt_hash(self):
        password_hash = self.user["password_hash"]
        self.assertIsInstance(password_hash, str)
        self.assertTrue(password_hash.startswith("scrypt:"))
        self.assertNotEqual(password_hash, "Example password!")
        self.assertTrue(check_password_hash(password_hash, "Example password!"))
        self.assertFalse(check_password_hash(password_hash, "Incorrect password!"))

    def test_each_document_has_a_fresh_id_and_password_salt(self):
        second_user = build_user_document("alice_123", "Example password!")
        self.assertNotEqual(second_user["_id"], self.user["_id"])
        self.assertNotEqual(second_user["password_hash"], self.user["password_hash"])
        first_salt = self.user["password_hash"].split("$")[1]
        second_salt = second_user["password_hash"].split("$")[1]
        self.assertNotEqual(first_salt, second_salt)
        self.assertTrue(
            check_password_hash(second_user["password_hash"], "Example password!")
        )

    def test_document_creation_rejects_invalid_usernames(self):
        for username in ("ab", "a" * 31, "ali ce", "Kelvin", None):
            with self.subTest(username=username):
                with self.assertRaises(ValueError):
                    build_user_document(username, "Example password!")

    def test_document_creation_rejects_empty_or_non_string_passwords(self):
        for password in ("", None, 123, True, b"password", ["password"]):
            with self.subTest(password=password):
                with self.assertRaises(ValueError):
                    build_user_document("alice", password)

    def test_password_keeps_case_and_outer_spaces_without_extra_length_rules(self):
        user = build_user_document("alice", " AbC ")
        self.assertTrue(verify_user_password(user, " AbC "))
        self.assertFalse(verify_user_password(user, "AbC"))
        self.assertFalse(verify_user_password(user, " abc "))
        short_password_user = build_user_document("alice", "x")
        self.assertTrue(verify_user_password(short_password_user, "x"))
        whitespace_password_user = build_user_document("alice", " ")
        self.assertTrue(verify_user_password(whitespace_password_user, " "))


class PasswordVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user = build_user_document("alice", "Example password!")

    def test_correct_password_verifies(self):
        self.assertIs(verify_user_password(self.user, "Example password!"), True)

    def test_incorrect_password_fails(self):
        self.assertIs(verify_user_password(self.user, "Incorrect password!"), False)

    def test_invalid_password_input_fails(self):
        for password in ("", None, 123, True, b"password", ["password"]):
            with self.subTest(password=password):
                self.assertIs(verify_user_password(self.user, password), False)


class UserLookupTests(unittest.TestCase):
    def test_lookup_uses_normalized_username_in_users_collection(self):
        database = MagicMock()
        expected_user = {"_id": ObjectId(), "username": "alice_123"}
        database["users"].find_one.return_value = expected_user

        result = find_user_by_username(database, " \tAlice_123\n")

        self.assertIs(result, expected_user)
        database["users"].find_one.assert_called_once_with({"username": "alice_123"})

    def test_creation_and_lookup_apply_the_same_username_rules(self):
        database = MagicMock()
        user = build_user_document("  Alice_123  ", "Example password!")
        database["users"].find_one.return_value = user

        result = find_user_by_username(database, "\tALICE_123\n")

        self.assertIs(result, user)
        database["users"].find_one.assert_called_once_with(
            {"username": user["username"]}
        )

    def test_invalid_username_is_rejected_before_database_query(self):
        for username in ("ab", "a" * 31, "ali ce", "Kelvin", None):
            with self.subTest(username=username):
                database = MagicMock()
                with self.assertRaises(ValueError):
                    find_user_by_username(database, username)
                database["users"].find_one.assert_not_called()


if __name__ == "__main__":
    unittest.main()
