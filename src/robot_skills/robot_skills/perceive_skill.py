"""Perception skill: describes the latest camera frame using Qwen2.5-VL."""

import base64
import io
import json

import ollama
from cv_bridge import CvBridge
from PIL import Image as PILImage
from sensor_msgs.msg import Image

PERCEIVE_PROMPT = """You are the vision system of a mobile robot. Analyze the attached
camera image and list every distinct object you can identify.

Respond with ONLY valid JSON in this exact shape, no other text:
{{
  "objects": [
    {{"label": "chair", "confidence": 0.9, "description": "a wooden chair near a table"}}
  ]
}}

User query: {query}
"""


class PerceiveSkill:
    """Analyzes the latest camera frame using Qwen2.5-VL via Ollama.

    Args:
        base_url: Ollama server URL.
        vision_model: Vision model name, e.g. "qwen2.5vl:7b".
    """

    def __init__(self, base_url: str, vision_model: str) -> None:
        self._client = ollama.Client(host=base_url)
        self._model = vision_model
        self._bridge = CvBridge()

    def describe(self, image_msg: Image, query: str) -> dict:
        """Runs Qwen2.5-VL over a ROS Image message.

        Args:
            image_msg: Latest camera frame.
            query: Natural language query guiding what to look for.

        Returns:
            Dict with "objects": list of {"label", "confidence", "description"}.

        Raises:
            ValueError: If the model response is not valid JSON.
        """
        cv_image = self._bridge.imgmsg_to_cv2(image_msg, desired_encoding='rgb8')
        pil_image = PILImage.fromarray(cv_image)
        buffer = io.BytesIO()
        pil_image.save(buffer, format='PNG')
        b64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')

        response = self._client.chat(
            model=self._model,
            messages=[{
                'role': 'user',
                'content': PERCEIVE_PROMPT.format(query=query),
                'images': [b64_image],
            }],
        )
        content = _strip_code_fence(response['message']['content'].strip())
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Invalid JSON from vision model: {content}') from exc


def _strip_code_fence(text: str) -> str:
    if not text.startswith('```'):
        return text
    lines = text.splitlines()[1:]
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines).strip()
