# -*- coding: utf-8 -*-
"""ICP备案查询服务模块。

提供ICP备案查询、滑块验证码识别等功能。
滑块验证码识别使用纯图像算法（numpy + PIL），无需模型文件。
"""

import asyncio
import io
import base64
import json
import time
import hashlib
import random
import uuid
from typing import Optional, Dict, Any, Tuple, TYPE_CHECKING

import numpy as np
from PIL import Image
import httpx

from utils import logger

if TYPE_CHECKING:
    from services.config_service import ConfigService


class ICPService:
    """ICP备案查询服务类。

    使用滑块验证码（纯图像算法，无需ONNX模型）完成验证后查询工信部ICP备案信息。
    """

    HOME = "https://beian.miit.gov.cn/"
    AUTH_URL = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/auth"
    GET_CAPTCHA = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/image/getCheckImagePoint"
    CHECK_CAPTCHA = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/image/checkImage"
    QUERY_URL = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/icpAbbreviateInfo/queryByCondition"
    DETAIL_URL = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/icpAbbreviateInfo/queryDetailByAppAndMiniId"

    QUERY_TYPES = {
        "web": {"pageNum": "", "pageSize": "", "unitName": "", "serviceType": 1},
        "app": {"pageNum": "", "pageSize": "", "unitName": "", "serviceType": 6},
        "mapp": {"pageNum": "", "pageSize": "", "unitName": "", "serviceType": 7},
        "kapp": {"pageNum": "", "pageSize": "", "unitName": "", "serviceType": 8},
    }

    def __init__(self, config_service: Optional['ConfigService'] = None):
        self.config_service = config_service
        self.token = ""
        self.token_expire = 0
        self.session: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # HTTP session
    # ------------------------------------------------------------------

    async def get_session(self) -> httpx.AsyncClient:
        if self.session is None or self.session.is_closed:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self.session

    async def close(self):
        if self.session and not self.session.is_closed:
            await self.session.aclose()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_client_uid() -> str:
        characters = "0123456789abcdef"
        uid = ["0"] * 36
        for i in range(36):
            uid[i] = random.choice(characters)
        uid[14] = "4"
        uid[19] = characters[(3 & int(uid[19], 16)) | 8]
        uid[8] = uid[13] = uid[18] = uid[23] = "-"
        return json.dumps({"clientUid": "point-" + "".join(uid)})

    def _make_base_headers(self, token: str = "") -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/101.0.4951.41 Safari/537.36 Edg/101.0.1210.32",
            "Origin": "https://beian.miit.gov.cn",
            "Referer": "https://beian.miit.gov.cn/",
            "Cookie": f"__jsluid_s={uuid.uuid4().hex}",
            "Accept": "application/json, text/plain, */*",
        }
        if token:
            headers["Token"] = token
        return headers

    @staticmethod
    def _extract_sign(params: Any) -> str:
        if isinstance(params, dict):
            v = params.get("sign", "")
            return v if isinstance(v, str) else ""
        if isinstance(params, str):
            return params
        return ""

    # ------------------------------------------------------------------
    # 滑块验证码 — 纯图像算法（参考 ymicp.py match_slider_offset）
    # ------------------------------------------------------------------

    @staticmethod
    def match_slider_offset(small_image_b64: str, big_image_b64: str) -> Tuple[bool, Any]:
        """在大图上找滑块缺口位置，返回 (成功, x偏移量 或 错误信息)。

        算法：缩小大图到一半 → 量化颜色 → 找高频纯色 → 检测近似正方形连续区域
        """
        big_img = np.array(
            Image.open(io.BytesIO(base64.b64decode(big_image_b64))).convert("RGB")
        )
        small_img = np.array(
            Image.open(io.BytesIO(base64.b64decode(small_image_b64)))
        )
        sh, sw = small_img.shape[:2]

        resized = big_img[::2, ::2]
        h, w = resized.shape[:2]
        min_side = int(min(sw, sh) * 0.5 * 0.5)

        q = (resized.astype(np.int32) // 4) * 4
        color_id = q[:, :, 0] + q[:, :, 1] * 256 + q[:, :, 2] * 65536

        flat_colors = color_id.ravel()
        unique, counts = np.unique(flat_colors, return_counts=True)
        top_indices = np.argsort(counts)[-5:]

        best_area = 0
        best_x = 0

        for idx in top_indices:
            c = unique[idx]
            mask = color_id == c

            col_run = np.zeros((h, w), dtype=np.int32)
            col_run[0] = mask[0].astype(np.int32)
            for y in range(1, h):
                col_run[y] = np.where(mask[y], col_run[y - 1] + 1, 0)

            for y in range(min_side, h):
                row = col_run[y] >= min_side
                if not np.any(row):
                    continue
                d = np.diff(row.astype(np.int8))
                starts = np.where(d == 1)[0] + 1
                ends = np.where(d == -1)[0] + 1
                if row[0]:
                    starts = np.concatenate([[0], starts])
                if row[-1]:
                    ends = np.concatenate([ends, [w]])
                for s, e in zip(starts, ends):
                    run_w = e - s
                    if s <= sw // 4:
                        continue
                    run_h = int(col_run[y, s])
                    ratio = run_w / run_h if run_h > 0 else 0
                    if 0.7 < ratio < 1.4 and run_w * run_h > best_area:
                        best_area = run_w * run_h
                        best_x = s

        if best_area == 0:
            return False, "未找到缺口"

        offset_x = best_x * 2
        logger.info(f"滑块缺口定位: x={offset_x}, 滑块尺寸={sw}x{sh}")
        return True, offset_x

    # ------------------------------------------------------------------
    # 认证 token
    # ------------------------------------------------------------------

    async def get_auth_token(self) -> Optional[str]:
        try:
            if self.token and time.time() < self.token_expire:
                return self.token

            ts = round(time.time() * 1000)
            auth_key = hashlib.md5(f"testtest{ts}".encode("UTF-8")).hexdigest()

            session = await self.get_session()
            headers = self._make_base_headers()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

            resp = await session.post(
                self.AUTH_URL, headers=headers,
                data={"authKey": auth_key, "timeStamp": ts},
            )
            if resp.status_code != 200:
                logger.error(f"获取token失败 HTTP {resp.status_code}")
                return None

            data = resp.json()
            if not data.get("success"):
                logger.error(f"获取token失败: {data.get('msg')}")
                return None

            params = data.get("params", {})
            self.token = params.get("bussiness", "")
            if not self.token:
                logger.error("token为空")
                return None

            expire_ms = params.get("expire", 600000)
            self.token_expire = time.time() + expire_ms / 1000
            logger.info(f"获取ICP认证token成功（有效期{expire_ms/1000}s）")
            return self.token

        except Exception as e:
            logger.error(f"获取认证token异常: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # 获取验证码
    # ------------------------------------------------------------------

    async def get_captcha(self) -> Optional[Tuple[str, str, str]]:
        """获取滑块验证码图片。

        Returns:
            (big_image_b64, small_image_b64, captcha_uuid) 或 None
        """
        try:
            token = await self.get_auth_token()
            if not token:
                return None

            client_uid_data = self._get_client_uid()
            session = await self.get_session()
            headers = self._make_base_headers(token)
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(client_uid_data.encode("utf-8")))

            resp = await session.post(
                self.GET_CAPTCHA, headers=headers, data=client_uid_data,
            )
            if resp.status_code != 200:
                logger.error(f"获取验证码HTTP {resp.status_code}")
                return None

            data = resp.json()
            if not data.get("success"):
                logger.error(f"获取验证码失败: {data.get('msg')}")
                return None

            params = data.get("params", {})
            big_b64 = params.get("bigImage", "")
            small_b64 = params.get("smallImage", "")
            captcha_uuid = params.get("uuid", "")

            if not big_b64 or not small_b64 or not captcha_uuid:
                logger.error("验证码图片数据或uuid缺失")
                return None

            logger.info(f"获取验证码成功, uuid={captcha_uuid}")
            return big_b64, small_b64, captcha_uuid

        except Exception as e:
            logger.error(f"获取验证码异常: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # 验证滑块
    # ------------------------------------------------------------------

    async def verify_slider(self, captcha_uuid: str, offset_x: int) -> Tuple[bool, str]:
        """提交滑块偏移量验证。

        Returns:
            (成功, sign字符串)
        """
        try:
            token = await self.get_auth_token()
            if not token:
                return False, ""

            check_data = json.dumps({"key": captcha_uuid, "value": str(offset_x)})
            session = await self.get_session()
            headers = self._make_base_headers(token)
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(check_data.encode("utf-8")))

            resp = await session.post(
                self.CHECK_CAPTCHA, headers=headers, data=check_data,
            )
            if resp.status_code != 200:
                logger.error(f"验证滑块HTTP {resp.status_code}")
                return False, ""

            data = resp.json()
            success = data.get("success", False)
            if not success:
                logger.error(f"滑块验证失败: {data.get('msg')}")
                return False, ""

            sign = self._extract_sign(data.get("params", ""))
            logger.info("滑块验证成功")
            return True, sign

        except Exception as e:
            logger.error(f"验证滑块异常: {e}", exc_info=True)
            return False, ""

    # ------------------------------------------------------------------
    # ICP 查询
    # ------------------------------------------------------------------

    async def query_icp(
        self,
        query_type: str,
        search: str,
        page_num: int = 1,
        page_size: int = 20,
        max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """查询ICP备案信息（滑块验证码流程）。"""
        for retry in range(max_retries):
            try:
                logger.info(f"ICP查询 ({retry+1}/{max_retries}): {search}")

                # 1. 获取验证码
                captcha = await self.get_captcha()
                if not captcha:
                    await asyncio.sleep(1)
                    continue
                big_b64, small_b64, captcha_uuid = captcha

                # 2. 滑块匹配
                ok, result = await asyncio.to_thread(
                    self.match_slider_offset, small_b64, big_b64,
                )
                if not ok:
                    logger.error(f"滑块匹配失败: {result}")
                    await asyncio.sleep(1)
                    continue
                offset_x = result

                # 3. 验证
                verified, sign = await self.verify_slider(captcha_uuid, offset_x)
                if not verified:
                    await asyncio.sleep(1)
                    continue

                # 4. 查询
                session = await self.get_session()
                headers = self._make_base_headers(self.token)
                headers.update({
                    "Content-Type": "application/json",
                    "Sign": sign,
                    "Uuid": captcha_uuid,
                })

                qp = self.QUERY_TYPES.get(query_type, self.QUERY_TYPES["web"]).copy()
                qp["pageNum"] = str(page_num)
                qp["pageSize"] = str(page_size)
                qp["unitName"] = search

                resp = await session.post(self.QUERY_URL, headers=headers, json=qp)
                if resp.status_code != 200:
                    logger.error(f"查询HTTP {resp.status_code}")
                    await asyncio.sleep(1)
                    continue

                data = resp.json()
                if data.get("success"):
                    logger.info(f"ICP查询成功: {search}")
                    return data.get("params", {})

                logger.error(f"查询失败: {data.get('msg')}")
                await asyncio.sleep(1)

            except httpx.TimeoutException:
                logger.error(f"查询超时 ({retry+1}/{max_retries})")
                await asyncio.sleep(2)
            except httpx.NetworkError as e:
                logger.error(f"网络错误 ({retry+1}/{max_retries}): {e}")
                await asyncio.sleep(2)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (400, 401, 403, 404):
                    return None
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"查询异常 ({retry+1}/{max_retries}): {e}", exc_info=True)
                await asyncio.sleep(1)

        logger.error(f"查询失败，重试{max_retries}次: {search}")
        return None

    # ------------------------------------------------------------------
    # 详情查询
    # ------------------------------------------------------------------

    async def get_detail_info(
        self, data_id: str, service_type: int,
    ) -> Optional[Dict[str, Any]]:
        try:
            session = await self.get_session()
            headers = self._make_base_headers(self.token)
            headers["Content-Type"] = "application/json"

            resp = await session.post(
                self.DETAIL_URL, headers=headers,
                json={"dataId": data_id, "serviceType": service_type},
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    return data.get("params", {})
                logger.error(f"详情查询失败: {data.get('msg')}")
            else:
                logger.error(f"详情查询HTTP {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"详情查询异常: {e}")
            return None

    # ------------------------------------------------------------------
    # 结果格式化
    # ------------------------------------------------------------------

    def format_icp_result(self, result: Dict[str, Any]) -> str:
        if "params" in result and isinstance(result["params"], dict):
            data_list = result["params"].get("list", [])
            total = result["params"].get("total", 0)
            current_page = result["params"].get("pageNum", 1)
            total_pages = result["params"].get("pages", 1)
        else:
            data_list = result.get("list", [])
            total = result.get("total", 0)
            current_page = result.get("pageNum", 1)
            total_pages = result.get("pages", 1)

        if not data_list:
            return "未查询到相关备案信息"

        lines = [
            f"共查询到 {total} 条结果",
            f"当前第 {current_page} 页，共 {total_pages} 页",
            "-" * 60,
        ]
        for idx, item in enumerate(data_list, 1):
            lines.append(f"\n【记录 {idx}】")
            lines.append(f"主办单位: {item.get('unitName', 'N/A')}")
            lines.append(f"单位性质: {item.get('natureName', 'N/A')}")
            if "domain" in item:
                lines.append(f"域名: {item.get('domain', 'N/A')}")
            if "serviceName" in item:
                lines.append(f"服务名称: {item.get('serviceName', 'N/A')}")
            if "serviceHome" in item:
                lines.append(f"首页网址: {item.get('serviceHome', 'N/A')}")
            ml = item.get("mainLicence", "N/A")
            sl = item.get("serviceLicence", "N/A")
            if ml != "N/A":
                lines.append(f"主体备案号: {ml}")
            if sl != "N/A":
                lines.append(f"服务备案号: {sl}")
            lines.append(f"审核通过日期: {item.get('updateRecordTime', 'N/A')}")
            if "mainUnitAddress" in item:
                lines.append(f"主体地址: {item.get('mainUnitAddress', 'N/A')}")
            if "serviceContent" in item:
                lines.append(f"服务内容: {item.get('serviceContent', 'N/A')}")
            if "serviceScope" in item:
                lines.append(f"服务范围: {item.get('serviceScope', 'N/A')}")
            lines.append("-" * 60)
        return "\n".join(lines)

    def __del__(self):
        if self.session and not self.session.is_closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.session.aclose())
                else:
                    loop.run_until_complete(self.session.aclose())
            except Exception:
                pass
