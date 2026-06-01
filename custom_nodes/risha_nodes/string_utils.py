"""Small string utility nodes — concat, format, write-to-file.

rgthree has some of these, but having Risha-branded versions avoids
silent upstream breakage when rgthree changes node signatures."""

from __future__ import annotations

from pathlib import Path
import re


class RishaStringConcat:
    """Concatenate up to 4 strings with a configurable separator."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "separator": ("STRING", {"default": "\n\n"}),
                "a": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
            },
            "optional": {
                "b": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
                "c": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
                "d": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("joined",)
    FUNCTION = "go"
    CATEGORY = "Risha/Util"

    def go(self, separator: str, a: str, b: str = "", c: str = "", d: str = ""):
        parts = [p for p in (a, b, c, d) if p]
        return (separator.join(parts),)


class RishaStringFormat:
    """Format a Python-style template with up to 5 named {key} slots."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "template": ("STRING", {"multiline": True, "default": "{a} / {b}"}),
            },
            "optional": {
                "key_a": ("STRING", {"default": "a"}),
                "val_a": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key_b": ("STRING", {"default": "b"}),
                "val_b": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key_c": ("STRING", {"default": "c"}),
                "val_c": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key_d": ("STRING", {"default": "d"}),
                "val_d": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
                "key_e": ("STRING", {"default": "e"}),
                "val_e": ("STRING", {"default": "", "multiline": True, "forceInput": True}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("formatted",)
    FUNCTION = "go"
    CATEGORY = "Risha/Util"

    def go(self, template: str,
           key_a: str = "a", val_a: str = "",
           key_b: str = "b", val_b: str = "",
           key_c: str = "c", val_c: str = "",
           key_d: str = "d", val_d: str = "",
           key_e: str = "e", val_e: str = ""):
        out = template
        for k, v in [(key_a, val_a), (key_b, val_b), (key_c, val_c),
                     (key_d, val_d), (key_e, val_e)]:
            k = (k or "").strip()
            if k:
                out = out.replace("{" + k + "}", v or "")
        # Strip any leftover {placeholder} tokens so we don't ship them downstream
        out = re.sub(r"\{[A-Za-z0-9_]+\}", "", out)
        return (out,)


class RishaStringToFile:
    """Write a string to disk (creates parent dirs). OUTPUT_NODE so it executes."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "text": ("STRING", {"multiline": True, "forceInput": True}),
                "path": ("STRING", {"default": "/root/comfy/ComfyUI/output/risha_debug.txt"}),
                "mode": (["write", "append"], {"default": "write"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("path",)
    FUNCTION = "go"
    CATEGORY = "Risha/Util"
    OUTPUT_NODE = True

    def go(self, text: str, path: str, mode: str):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with p.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        else:
            p.write_text(text, encoding="utf-8")
        return (str(p),)
