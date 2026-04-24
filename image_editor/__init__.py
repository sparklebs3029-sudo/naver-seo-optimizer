import pathlib
import streamlit.components.v1 as components

_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"
_component_func = components.declare_component("image_editor", path=str(_FRONTEND_DIR))


def image_editor(products: list, images_b64: dict, action_result: dict = None, key: str = None):
    """Streamlit 이미지 편집기 컴포넌트.

    Args:
        products: load_xlsx()가 반환한 상품 리스트
        images_b64: {url: "data:image/...;base64,..."} 캐시 딕셔너리
        action_result: 직전 액션(Drive 업로드 등)의 결과 dict
        key: Streamlit 컴포넌트 키

    Returns:
        JS에서 요청한 액션 dict (action 필드 포함) 또는 None
    """
    return _component_func(
        products=products,
        images_b64=images_b64,
        action_result=action_result,
        key=key,
        default=None,
    )
