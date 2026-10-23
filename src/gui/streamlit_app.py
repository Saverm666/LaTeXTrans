"""Compatibility notice for the retired Streamlit launcher."""


def main():
    from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx
    if get_script_run_ctx(suppress_warning=True) is not None:
        import streamlit as st
        st.set_page_config(page_title="LaTeXTrans · 启动方式已更新")
        st.title("LaTeXTrans 已使用独立网页界面")
        st.write("请先停止此 Streamlit 进程，再使用下面的命令启动新工作台。")
        st.code("latextrans-gui", language="bash")
        st.write("默认地址仍为 http://localhost:8501。配置和翻译断点保持可用。")
    else:
        from src.gui.server import main as serve
        serve()


if __name__ == "__main__":
    main()
