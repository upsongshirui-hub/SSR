import requests
import json
import logging
import time
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

logger = logging.getLogger(__name__)

class DeepSeekClient:
    _MAX_RETRIES = 2
    _RETRY_DELAY = 1.5

    @staticmethod
    def analyze(telemetry_data, neo4j_knowledge):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        system_prompt = (
            "你是一位资深的油气勘探专家与IIoT诊断助手。"
            "请结合实时数据与图谱经验，给出简明（≤150字）分析与操作建议。"
        )
        user_prompt = (
            f"【实时数据】\n{json.dumps(telemetry_data, indent=2, ensure_ascii=False)}\n\n"
            f"【知识图谱】\n{neo4j_knowledge}\n\n诊断结果与建议："
        )
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3,
            "stream": False
        }

        for attempt in range(DeepSeekClient._MAX_RETRIES + 1):
            try:
                response = requests.post(
                    DEEPSEEK_BASE_URL, headers=headers, json=payload, timeout=30
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
            except requests.exceptions.Timeout:
                if attempt < DeepSeekClient._MAX_RETRIES:
                    logger.warning(f"DeepSeek 请求超时，{DeepSeekClient._RETRY_DELAY}s 后重试 ({attempt+1}/{DeepSeekClient._MAX_RETRIES})")
                    time.sleep(DeepSeekClient._RETRY_DELAY)
                else:
                    return "⚠️ DeepSeek API 超时，请检查网络或稍后重试。"
            except requests.exceptions.HTTPError as e:
                return f"❌ DeepSeek API 返回错误: {e.response.status_code} - {e.response.text}"
            except Exception as e:
                if attempt < DeepSeekClient._MAX_RETRIES:
                    logger.warning(f"DeepSeek 请求异常，重试中: {e}")
                    time.sleep(DeepSeekClient._RETRY_DELAY)
                else:
                    return f"❌ DeepSeek API 调用失败: {e}"

    @staticmethod
    def interactive_analyze(prompt):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }
        try:
            response = requests.post(DEEPSEEK_BASE_URL, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout:
            return "⚠️ DeepSeek API 超时，请重试。"
        except Exception as e:
            return f"❌ DeepSeek API 失败: {e}"