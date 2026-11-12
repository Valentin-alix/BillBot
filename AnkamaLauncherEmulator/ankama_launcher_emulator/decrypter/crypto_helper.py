import base64
import hashlib
import json
import logging
import os
from collections.abc import Mapping

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad
from pydantic import BaseModel

from ankama_launcher_emulator.controller.bot_storage import (
    BotStorageController,
)
from ankama_launcher_emulator.decrypter.device import Device
from ankama_launcher_emulator.interfaces.credentials import (
    DecipheredApiKey,
    DecipheredCertif,
    StoredApiKey,
    StoredCertificate,
)
from ankama_launcher_emulator.interfaces.local_storage import BotRecord

logger = logging.getLogger()


class CryptoHelper:
    @staticmethod
    def getStoredCertificate(login: str) -> StoredCertificate:
        record = BotStorageController().get_record(login)
        if record is None or record.encrypted_api_key is None:
            raise FileNotFoundError(f"No stored certificate for {login}")
        raw = CryptoHelper.decrypt(record.encrypted_api_key, Device.getUUID())
        certificate = DecipheredApiKey.model_validate_json(raw).certificate
        if certificate is None:
            raise FileNotFoundError(f"No stored certificate for {login}")
        return StoredCertificate(certificate=certificate)

    @staticmethod
    def remove_bot(login: str) -> None:
        storage = BotStorageController()
        if storage.get_record(login) is None:
            return

        def clear_auth(record: BotRecord) -> None:
            record.encrypted_api_key = None

        storage.update_record(login, clear_auth)

    @staticmethod
    def getStoredApiKeys(records: Mapping[str, BotRecord] | None = None) -> list[StoredApiKey]:
        stored_api_keys: list[StoredApiKey] = []
        records = BotStorageController().get_all_records() if records is None else records
        for record in records.values():
            if record.encrypted_api_key is None:
                continue
            raw = CryptoHelper.decrypt(
                record.encrypted_api_key,
                Device.getUUID(),
            )
            stored_api_keys.append(
                StoredApiKey(
                    apikey=DecipheredApiKey.model_validate_json(raw),
                )
            )
        return stored_api_keys

    @staticmethod
    def getStoredApiKey(login: str) -> StoredApiKey:
        return next(stored for stored in CryptoHelper.getStoredApiKeys() if stored.apikey.login == login)

    @staticmethod
    def decrypt(data: str, uuid: str) -> str:
        splitted_datas = data.split("|")
        iv = bytes.fromhex(splitted_datas[0])
        data_to_decrypt = bytes.fromhex(splitted_datas[1])

        key = CryptoHelper.createHashFromString(uuid)

        decipher = AES.new(key, AES.MODE_CBC, iv)

        decrypted_data = decipher.decrypt(data_to_decrypt)
        decrypted_data = unpad(decrypted_data, AES.block_size)
        return decrypted_data.decode("utf-8")

    @staticmethod
    def encrypt(payload: BaseModel | str, uuid: str) -> str:
        key = CryptoHelper.createHashFromString(uuid)
        iv = os.urandom(16)
        cipher = AES.new(key, AES.MODE_CBC, iv)

        serialized = payload.model_dump_json() if isinstance(payload, BaseModel) else json.dumps(payload)
        padded_data = pad(serialized.encode("utf-8"), AES.block_size)

        encrypted_data = cipher.encrypt(padded_data)

        return iv.hex() + "|" + encrypted_data.hex()

    @staticmethod
    def createHashFromStringSha(string: str) -> str:
        return hashlib.sha256(string.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def createHashFromString(string: str) -> bytes:
        return hashlib.md5(string.encode("utf-8")).digest()

    @staticmethod
    def createHmEncoders() -> tuple[str, str]:
        arch = Device.getArch()
        plt = Device.getPlatform()
        machine_id = Device.getMachineId(arch)
        username = Device.getUsername()
        os_version = Device.getOsVersion()
        ram = Device.getComputerRam()
        machine_infos = [
            arch,
            plt,
            machine_id,
            username,
            str(int(os_version)),
            str(ram),
        ]
        hm1 = CryptoHelper.createHashFromStringSha("".join(machine_infos))
        hm2 = hm1[::-1]
        return hm1, hm2

    @staticmethod
    def generateHashFromCertif(certif: DecipheredCertif) -> str:
        hm1, hm2 = CryptoHelper.createHmEncoders()

        decipher = AES.new(hm2.encode(), AES.MODE_ECB)

        decoded_certificate = base64.b64decode(certif.encodedCertificate)
        decrypted_certificate = decipher.decrypt(decoded_certificate)

        try:
            decrypted_certificate = unpad(decrypted_certificate, AES.block_size)
        except ValueError:
            # Ankama certificates may lack PKCS7 padding; hashing must still match the launcher.
            pass

        combined_datas = hm1.encode() + decrypted_certificate
        return hashlib.sha256(combined_datas).hexdigest()

    @staticmethod
    def store_api_key(login: str, api_key_data: DecipheredApiKey) -> None:
        assert api_key_data.login == login, "Stored API key login must match target login"
        encrypted_api_key = CryptoHelper.encrypt(
            api_key_data,
            Device.getUUID(),
        )
        BotStorageController().update_record(
            login,
            lambda record: setattr(record, "encrypted_api_key", encrypted_api_key),
        )
        logger.info("[OAuth] API key stored for %s", login)
