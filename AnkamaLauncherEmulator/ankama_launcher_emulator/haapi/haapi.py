import getpass
import logging
from dataclasses import dataclass
from datetime import datetime
from time import time

import requests
import urllib3

from ankama_launcher_emulator.controller.bot_storage import (
    BotStorageController,
)
from ankama_launcher_emulator.decrypter.crypto_helper import (
    CryptoHelper,
)
from ankama_launcher_emulator.decrypter.device import Device
from ankama_launcher_emulator.haapi.urls import (
    ANKAMA_ACCOUNT_CREATE_TOKEN,
    ANKAMA_ACCOUNT_SET_NICKNAME_WITH_API_KEY,
    ANKAMA_ACCOUNT_SIGN_ON_WITH_API_KEY,
    ANKAMA_API_REFRESH_API_KEY,
    ANKAMA_SHIELD_SECURITY_CODE,
    ANKAMA_SHIELD_VALIDATE_CODE,
    ANKAMA_SHIELD_VALIDATE_OTP,
)
from ankama_launcher_emulator.haapi.zaap_version import (
    get_zaap_version,
)
from ankama_launcher_emulator.interfaces.credentials import (
    DecipheredApiKey,
    DecipheredCertif,
)
from ankama_launcher_emulator.interfaces.game import GameIdEnum
from ankama_launcher_emulator.interfaces.haapi_api import (
    CertificateResponse,
    CreateTokenResponse,
    GameRequest,
    RefreshApiKeyRequest,
    RefreshApiKeyResponse,
    SecurityCodeResponse,
    SignOnResponse,
)
from ankama_launcher_emulator.interfaces.local_storage import BotRecord
from ankama_launcher_emulator.interfaces.zaap_files import (
    GameSubscription,
    UserAccount,
)
from ankama_launcher_emulator.utils.internet import (
    raise_for_status_with_content,
    retry_internet,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class ShieldNotRequiredError(Exception):
    pass


def _default_dofus_subscription() -> GameSubscription:
    return GameSubscription(
        isFreeToPlay=True,
        isFormerSubscriber=False,
        isSubscribed=False,
        totalPlayTime=0,
        endOfSubscribe=None,
        id=GameIdEnum.DOFUS,
    )


def upsert_settings_account(login: str, account: UserAccount) -> None:
    if not account.game_list:
        account.game_list.append(_default_dofus_subscription())
    BotStorageController().upsert_account_info(login, account)
    logger.info("[OAuth] Account information updated for %s", login)


def refresh_api_key_from_oauth(
    access_token: str,
    oauth_refresh_token: str,
    cert_id: int | None = None,
    cert_hash: str | None = None,
    proxy_url: str | None = None,
) -> RefreshApiKeyResponse:
    """Use the OAuth access token as the temporary apikey and exchange the refresh token for a HAAPI key."""
    session = requests.Session()
    if proxy_url is not None:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    headers = {
        "apikey": access_token,
        "user-Agent": f"Zaap {get_zaap_version()}",
        "accept": "*/*",
        "accept-encoding": "gzip,deflate",
        "sec-fetch-site": "none",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-dest": "empty",
        "accept-language": "fr",
    }
    payload = RefreshApiKeyRequest(
        refresh_token=oauth_refresh_token,
        certificate_id=cert_id,
        certificate_hash=cert_hash,
    )
    response = session.post(
        ANKAMA_API_REFRESH_API_KEY,
        data=payload.to_form(),
        headers=headers,
        verify=False,
        timeout=HAAPI_HTTP_TIMEOUT,
    )
    response_body = raise_for_status_with_content(response)
    return RefreshApiKeyResponse.model_validate(response_body)


def get_account_info_by_login(login: str) -> UserAccount | None:
    record = BotStorageController().get_record(login)
    return record.account_info if record is not None else None


def get_game_sub_info_by_login(login: str) -> GameSubscription:
    account_info = get_account_info_by_login(login)
    dofus_game = (
        next(
            (game for game in account_info.game_list if game.id == GameIdEnum.DOFUS),
            None,
        )
        if account_info
        else None
    )
    if not dofus_game:
        return _default_dofus_subscription()
    dofus_game.is_former_subscriber = (
        dofus_game.end_of_subscribe is not None
        and dofus_game.end_of_subscribe > datetime(2000, 1, 1, tzinfo=dofus_game.end_of_subscribe.tzinfo)
    )
    return dofus_game


logger = logging.getLogger()

API_KEY_REFRESH_INTERVAL_MS = 172_800_000
HAAPI_HTTP_TIMEOUT = (10, 30)


@dataclass
class Haapi:
    api_key: str
    login: str
    proxy_url: str | None = None

    def __post_init__(self) -> None:
        self.zaap_session = requests.Session()
        if self.proxy_url:
            self.zaap_session.proxies = {
                "http": self.proxy_url,
                "https": self.proxy_url,
            }
        self.zaap_headers = {
            "apikey": self.api_key,
            "if-none-match": "null",
            "user-Agent": f"Zaap {get_zaap_version()}",
            "accept": "*/*",
            "accept-encoding": "gzip,deflate",
            "sec-fetch-site": "none",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-dest": "empty",
            "accept-language": "fr",
        }
        self.zaap_session.headers.update(self.zaap_headers)

    @retry_internet
    def signOnWithApiKey(self, game_id: int) -> SignOnResponse:
        url = ANKAMA_ACCOUNT_SIGN_ON_WITH_API_KEY
        response = self.zaap_session.post(
            url, json=GameRequest(game=game_id).model_dump(), verify=False, timeout=HAAPI_HTTP_TIMEOUT
        )
        raise_for_status_with_content(response)
        parsed = SignOnResponse.model_validate(response.json())
        upsert_settings_account(self.login, parsed.account)
        return parsed

    def set_nickname_with_api_key(self, nickname: str, lang: str = "fr") -> UserAccount:
        response = self.zaap_session.post(
            ANKAMA_ACCOUNT_SET_NICKNAME_WITH_API_KEY,
            data={"nickname": nickname, "lang": lang},
            verify=False,
            timeout=HAAPI_HTTP_TIMEOUT,
        )
        raise_for_status_with_content(response)
        account = UserAccount.model_validate(response.json())
        upsert_settings_account(self.login, account)
        return account

    def get_security_code(self, transport_type: str = "EMAIL") -> str:
        response = self.zaap_session.get(
            ANKAMA_SHIELD_SECURITY_CODE,
            params={"transportType": transport_type},
            verify=False,
            timeout=HAAPI_HTTP_TIMEOUT,
        )
        if response.status_code == 401 and "NONEEDTOBESECURED" in response.text:
            raise ShieldNotRequiredError
        raise_for_status_with_content(response)
        return SecurityCodeResponse.model_validate(response.json()).domain or ""

    def validate_code(self, code: str, game_id: int) -> DecipheredCertif:
        return self._validate_shield_code(ANKAMA_SHIELD_VALIDATE_CODE, code, game_id)

    def validate_otp(self, code: str, game_id: int) -> DecipheredCertif:
        return self._validate_shield_code(ANKAMA_SHIELD_VALIDATE_OTP, code, game_id)

    def _validate_shield_code(self, url: str, code: str, game_id: int) -> DecipheredCertif:
        hm1, hm2 = CryptoHelper.createHmEncoders()
        name = f"launcher-{getpass.getuser()}"
        response = self.zaap_session.get(
            url,
            params={
                "game_id": game_id,
                "code": code,
                "hm1": hm1,
                "hm2": hm2,
                "name": name,
            },
            verify=False,
            timeout=HAAPI_HTTP_TIMEOUT,
        )
        raise_for_status_with_content(response)
        parsed = CertificateResponse.model_validate(response.json())
        return DecipheredCertif(
            id=parsed.id,
            encodedCertificate=parsed.encodedCertificate,
            login=self.login,
        )

    def refresh_api_key(self) -> None:
        def do_refresh(record: BotRecord) -> None:
            if record.encrypted_api_key is None:
                raise FileNotFoundError(f"No stored certificate for {self.login}")
            raw = CryptoHelper.decrypt(record.encrypted_api_key, Device.getUUID())
            api_key = DecipheredApiKey.model_validate_json(raw)
            now_ms = int(time() * 1000)
            if api_key.refreshDate + API_KEY_REFRESH_INTERVAL_MS > now_ms:
                return

            response = self.zaap_session.post(
                ANKAMA_API_REFRESH_API_KEY,
                data=RefreshApiKeyRequest(refresh_token=api_key.refreshToken).to_form(),
                verify=False,
                timeout=HAAPI_HTTP_TIMEOUT,
            )
            response_body = raise_for_status_with_content(response)
            parsed = RefreshApiKeyResponse.model_validate(response_body)

            api_key.refreshToken = parsed.refresh_token
            api_key.refreshDate = now_ms
            record.encrypted_api_key = CryptoHelper.encrypt(api_key, Device.getUUID())
            logger.info("[HAAPI] API key refreshed for %s", self.login)

        BotStorageController().update_record(self.login, do_refresh)

    @retry_internet
    def createToken(self, game_id: int, certif: DecipheredCertif | None) -> str:
        self.refresh_api_key()
        url = ANKAMA_ACCOUNT_CREATE_TOKEN
        params: dict[str, int | str] = {"game": game_id}
        if certif:
            params["certificate_id"] = certif.id
            params["certificate_hash"] = CryptoHelper.generateHashFromCertif(certif)
        response = self.zaap_session.get(url, params=params, verify=False, timeout=HAAPI_HTTP_TIMEOUT)
        raise_for_status_with_content(response)
        return CreateTokenResponse.model_validate(response.json()).token
