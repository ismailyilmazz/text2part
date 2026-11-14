import time

import gradio as gr
import spaces

from text2part.envcheck import run_checks


def env_report() -> dict:
    return run_checks()


@spaces.GPU(duration=20)
def gpu_probe() -> dict:
    started = time.perf_counter()
    try:
        import torch
    except ImportError as error:
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}
    result = {"torch": torch.__version__, "cuda": torch.cuda.is_available()}
    if result["cuda"]:
        matrix = torch.randn(2048, 2048, device="cuda")
        product = matrix @ matrix
        torch.cuda.synchronize()
        result["device"] = torch.cuda.get_device_name(0)
        result["checksum"] = round(float(product.abs().mean().item()), 3)
    result["ok"] = result["cuda"]
    result["seconds"] = round(time.perf_counter() - started, 3)
    return result


with gr.Blocks(title="text2part") as demo:
    gr.Markdown("text2part backend: environment probe")
    output = gr.JSON()
    gr.Button("Environment report").click(env_report, outputs=output, api_name="env_report")
    gr.Button("GPU probe").click(gpu_probe, outputs=output, api_name="gpu_probe")

if __name__ == "__main__":
    demo.launch(ssr_mode=False)
