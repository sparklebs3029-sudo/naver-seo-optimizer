import pathlib
import streamlit.components.v1 as components

_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"
_component_func = None

if _FRONTEND_DIR.exists():
    _component_func = components.declare_component("image_editor", path=str(_FRONTEND_DIR))


def image_editor(
    products: list,
    images_b64: dict | None = None,
    action_result: dict = None,
    saved_data: dict | None = None,
    ui_state: dict | None = None,
    key: str = None,
):
    """Streamlit 이미지 편집기 컴포넌트.

    Args:
        products: load_xlsx()가 반환한 상품 리스트
        images_b64: {url: "data:image/...;base64,..."} 캐시 딕셔너리
        action_result: 직전 액션(Drive 업로드 등)의 결과 dict
        key: Streamlit 컴포넌트 키

    Returns:
        JS에서 요청한 액션 dict (action 필드 포함) 또는 None
    """
    if _component_func is None:
        return None

    return _component_func(
        products=products,
        images_b64=images_b64 or {},
        action_result=action_result,
        saved_data=saved_data or {},
        ui_state=ui_state or {},
        key=key,
        default=None,
        height=820,
    )
