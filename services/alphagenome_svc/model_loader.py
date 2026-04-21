from __future__ import annotations

import logging
import threading
from pathlib import Path

import torch
from schema import load_settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()


def _bake_standardized_conv(w: torch.Tensor, scale: torch.Tensor, bias: torch.Tensor, min_scale: float = 1e-4):
    """Absorb StandardizedConv scale into weight (PyTorch layout: out, in, width)."""
    fan_in = w.shape[1] * w.shape[2]
    s = scale.reshape(-1, 1, 1)
    w_c = w - w.mean(dim=(1, 2), keepdim=True)
    var = w_c.var(dim=(1, 2), keepdim=True, unbiased=False)
    denom = torch.clamp_min((fan_in * var).to(w.dtype), min_scale)
    return (w_c * s * torch.rsqrt(denom)).contiguous(), bias.contiguous()


def _is_official_format(state_dict: dict) -> bool:
    return any(k.startswith("tower.") or k.startswith("encoder.") for k in state_dict)


def _convert_official_to_community(sd: dict) -> dict:
    """Map official AlphaGenome checkpoint keys to alphagenome_pytorch keys.

    The official checkpoint stores weight-standardized convolutions with a
    separate 'scale' tensor; this bakes the scale into the weight so a plain
    Conv1d can be used.
    """
    out: dict[str, torch.Tensor] = {}

    def get(k: str) -> torch.Tensor:
        return sd[k]

    def bake_into(src_prefix: str, dst_prefix: str) -> None:
        w, b = _bake_standardized_conv(
            get(f"{src_prefix}.conv.weight"),
            get(f"{src_prefix}.conv.scale"),
            get(f"{src_prefix}.conv.bias"),
        )
        out[f"{dst_prefix}.weight"] = w
        out[f"{dst_prefix}.bias"] = b

    def copy_norm(src: str, dst: str, beta_key: str = "bias", gamma_key: str = "weight") -> None:
        out[f"{dst}.beta"] = get(f"{src}.{beta_key}")
        out[f"{dst}.gamma"] = get(f"{src}.{gamma_key}")
        out[f"{dst}.running_var"] = get(f"{src}.running_var")

    # ── organism embedding ─────────────────────────────────────────────────
    out["organism_embed.embed.weight"] = get("organism_embed.weight")

    # ── encoder: DNA embedder ──────────────────────────────────────────────
    out["transformer_unet.dna_embed.conv.weight"] = get("encoder.dna_embedder.conv1.weight")
    out["transformer_unet.dna_embed.conv.bias"] = get("encoder.dna_embedder.conv1.bias")
    copy_norm("encoder.dna_embedder.block.norm", "transformer_unet.dna_embed.pointwise.net.0")
    bake_into("encoder.dna_embedder.block", "transformer_unet.dna_embed.pointwise.net.2")

    # ── encoder: down blocks ───────────────────────────────────────────────
    for i in range(6):
        copy_norm(f"encoder.down_blocks.{i}.block1.norm", f"transformer_unet.downs.{i}.conv.net.0")
        bake_into(f"encoder.down_blocks.{i}.block1", f"transformer_unet.downs.{i}.conv.net.2")
        copy_norm(f"encoder.down_blocks.{i}.block2.norm", f"transformer_unet.downs.{i}.conv_out.net.0")
        bake_into(f"encoder.down_blocks.{i}.block2", f"transformer_unet.downs.{i}.conv_out.net.2")

    # ── decoder: up blocks ─────────────────────────────────────────────────
    for i in range(7):
        copy_norm(f"decoder.up_blocks.{i}.conv_in.norm", f"transformer_unet.ups.{i}.conv.net.0")
        bake_into(f"decoder.up_blocks.{i}.conv_in", f"transformer_unet.ups.{i}.conv.net.2")
        copy_norm(f"decoder.up_blocks.{i}.conv_out.norm", f"transformer_unet.ups.{i}.conv_out.net.0")
        bake_into(f"decoder.up_blocks.{i}.conv_out", f"transformer_unet.ups.{i}.conv_out.net.2")
        copy_norm(f"decoder.up_blocks.{i}.pointwise.norm", f"transformer_unet.ups.{i}.unet_conv.net.0")
        # pointwise is a plain 1×1 conv (no scale)
        out[f"transformer_unet.ups.{i}.unet_conv.net.2.weight"] = get(f"decoder.up_blocks.{i}.pointwise.conv.weight")
        out[f"transformer_unet.ups.{i}.unet_conv.net.2.bias"] = get(f"decoder.up_blocks.{i}.pointwise.conv.bias")
        out[f"transformer_unet.ups.{i}.residual_scale"] = get(f"decoder.up_blocks.{i}.residual_scale")

    # ── transformer tower blocks ───────────────────────────────────────────
    for i in range(9):
        b = f"tower.blocks.{i}"
        l = f"transformer_unet.transformer.layers.{i}"

        # sub-module 0: multi-head attention
        copy_norm(f"{b}.mha.norm", f"{l}.0.pre_rmsnorm")
        copy_norm(f"{b}.mha.final_norm", f"{l}.0.post_rmsnorm")
        out[f"{l}.0.block.to_qkv.weight"] = torch.cat([
            get(f"{b}.mha.q_proj.weight"),
            get(f"{b}.mha.k_proj.weight"),
            get(f"{b}.mha.v_proj.weight"),
        ], dim=0)
        out[f"{l}.0.block.to_out.weight"] = get(f"{b}.mha.linear_embedding.weight")
        out[f"{l}.0.block.to_out.bias"] = get(f"{b}.mha.linear_embedding.bias")
        for part in ("q", "k", "v"):
            out[f"{l}.0.block.{part}_norm.weight"] = get(f"{b}.mha.norm_{part}.weight")
            out[f"{l}.0.block.{part}_norm.bias"] = get(f"{b}.mha.norm_{part}.bias")
        copy_norm(f"{b}.attn_bias.norm", f"{l}.0.block.to_attn_bias.0")
        out[f"{l}.0.block.to_attn_bias.2.weight"] = get(f"{b}.attn_bias.proj.weight")

        # sub-module 1: MLP
        copy_norm(f"{b}.mlp.norm", f"{l}.1.pre_rmsnorm")
        copy_norm(f"{b}.mlp.final_norm", f"{l}.1.post_rmsnorm")
        out[f"{l}.1.block.0.weight"] = get(f"{b}.mlp.fc1.weight")
        out[f"{l}.1.block.0.bias"] = get(f"{b}.mlp.fc1.bias")
        out[f"{l}.1.block.3.weight"] = get(f"{b}.mlp.fc2.weight")
        out[f"{l}.1.block.3.bias"] = get(f"{b}.mlp.fc2.bias")

        if i % 2 == 0:
            # sub-module 2: seq2pair
            sp = f"{b}.pair_update.seq2pair"
            out[f"{l}.2.norm.bias"] = get(f"{sp}.norm_seq2pair.bias")
            out[f"{l}.2.norm.weight"] = get(f"{sp}.norm_seq2pair.weight")
            out[f"{l}.2.qk_rel_pos_bias"] = torch.stack([get(f"{sp}.k_r_bias"), get(f"{sp}.q_r_bias")], dim=0)
            out[f"{l}.2.qk_to_pairwise.weight"] = get(f"{sp}.linear_pair.weight")
            out[f"{l}.2.qk_to_pairwise.bias"] = get(f"{sp}.linear_pair.bias")
            out[f"{l}.2.to_outer_sum.1.weight"] = torch.cat([get(f"{sp}.linear_y_k.weight"), get(f"{sp}.linear_y_q.weight")], dim=0)
            out[f"{l}.2.to_qk.weight"] = torch.cat([get(f"{sp}.linear_q.weight"), get(f"{sp}.linear_k.weight")], dim=0)
            out[f"{l}.2.to_rel_pos_encoding.weight"] = get(f"{sp}.linear_pos_features.weight")
            out[f"{l}.2.to_rel_pos_encoding.bias"] = get(f"{sp}.linear_pos_features.bias")

            # sub-module 3: row attention over pairwise
            ra = f"{b}.pair_update.row_attn"
            out[f"{l}.3.pre_rmsnorm.weight"] = get(f"{ra}.norm.weight")
            out[f"{l}.3.pre_rmsnorm.bias"] = get(f"{ra}.norm.bias")
            out[f"{l}.3.block.to_qk.weight"] = torch.cat([get(f"{ra}.linear_q.weight"), get(f"{ra}.linear_k.weight")], dim=0)
            out[f"{l}.3.block.to_v.weight"] = get(f"{ra}.linear_v.weight")
            out[f"{l}.3.block.to_v.bias"] = get(f"{ra}.linear_v.bias")

            # sub-module 4: pairwise MLP
            pm = f"{b}.pair_update.pair_mlp"
            out[f"{l}.4.pre_rmsnorm.weight"] = get(f"{pm}.norm.weight")
            out[f"{l}.4.pre_rmsnorm.bias"] = get(f"{pm}.norm.bias")
            out[f"{l}.4.block.0.weight"] = get(f"{pm}.linear1.weight")
            out[f"{l}.4.block.0.bias"] = get(f"{pm}.linear1.bias")
            out[f"{l}.4.block.3.weight"] = get(f"{pm}.linear2.weight")
            out[f"{l}.4.block.3.bias"] = get(f"{pm}.linear2.bias")

    # ── output embedders ───────────────────────────────────────────────────
    for src, dst in [("embedder_128bp", "outembed_128bp"), ("embedder_1bp", "outembed_1bp")]:
        out[f"{dst}.norm.beta"] = get(f"{src}.norm.bias")
        out[f"{dst}.norm.gamma"] = get(f"{src}.norm.weight")
        out[f"{dst}.norm.running_var"] = get(f"{src}.norm.running_var")
        out[f"{dst}.embed.weight"] = get(f"{src}.organism_embed.weight")
        out[f"{dst}.double_features.bias"] = get(f"{src}.project_in.bias")
        out[f"{dst}.double_features.weight"] = get(f"{src}.project_in.weight").squeeze(-1)

    out["outembed_1bp.skip_proj.weight"] = get("embedder_1bp.project_skip.weight").squeeze(-1)

    out["outembed_pair.norm.bias"] = get("embedder_pair.norm.bias")
    out["outembed_pair.norm.weight"] = get("embedder_pair.norm.weight")
    out["outembed_pair.embed.weight"] = get("embedder_pair.organism_embed.weight")

    return out


_HEAD_NAMES = ["atac", "dnase", "procap", "cage", "rna_seq", "chip_tf", "chip_histone"]
_HEAD_RESOLUTIONS = [1, 128]


class AlphaGenomePredictor:
    """Wraps the backbone + checkpoint head weights and exposes predict().

    The gtca/alphagenome_pytorch checkpoint stores per-organism head weights as
    [num_organisms, num_tracks, input_dim] tensors that don't match the
    community AlphaGenome ModuleDict head structure.  We apply them directly.
    """

    def __init__(self, backbone, raw_sd: dict, device: str):
        self.backbone = backbone
        self.device = device

        # Pre-index head weights: _heads[head][res] = (w, b, rs)
        self._heads: dict = {}
        for head in _HEAD_NAMES:
            for res in _HEAD_RESOLUTIONS:
                wk = f"heads.{head}.convs.{res}.weight"
                if wk not in raw_sd:
                    continue
                entry = self._heads.setdefault(head, {})
                entry[res] = (
                    raw_sd[wk],
                    raw_sd[f"heads.{head}.convs.{res}.bias"],
                    raw_sd.get(f"heads.{head}.residual_scales.{res}"),
                )

        # Contact maps (stored differently: [org, in_dim, num_tracks])
        self._cm_w = raw_sd.get("contact_maps_head.linear.weight")
        self._cm_b = raw_sd.get("contact_maps_head.linear.bias")

    def predict(self, dna, organism_index: int, heads: tuple, resolutions: tuple) -> dict:
        import torch
        import torch.nn.functional as F

        batch = dna.shape[0]
        org_t = torch.full((batch,), organism_index, device=self.device, dtype=torch.long)

        # Backbone expects integer indices [batch, seq_len]; convert from float one-hot if needed
        seq = dna.argmax(dim=-1).long() if dna.is_floating_point() else dna.long()
        embeds = self.backbone.get_embeds(seq, org_t)
        embeds_1bp, embeds_128bp, embeds_pair = embeds

        result: dict = {}
        for head_name in heads:
            if head_name not in self._heads:
                if head_name == "contact_maps" and self._cm_w is not None:
                    # Per-organism weight is [in_dim, num_tracks] (already transposed)
                    w = self._cm_w[organism_index].float().to(self.device)
                    b = self._cm_b[organism_index].float().to(self.device)
                    pair_f = embeds_pair.float()
                    # symmetrize (average with transpose)
                    pair_f = (pair_f + pair_f.transpose(1, 2)) * 0.5
                    result["contact_maps"] = pair_f @ w + b
                continue

            head_res_dict = self._heads[head_name]
            head_out: dict = {}
            for res in resolutions:
                if res not in head_res_dict:
                    continue
                w_raw, b_raw, rs_raw = head_res_dict[res]
                # Per-organism slice: [num_tracks, input_dim]
                w = w_raw[organism_index].float().to(self.device)
                b = b_raw[organism_index].float().to(self.device)

                x = (embeds_1bp if res == 1 else embeds_128bp).float()
                # [batch, seq_len, num_tracks]
                pred = x @ w.T + b
                if rs_raw is not None:
                    rs = rs_raw[organism_index].float().to(self.device)
                    pred = F.softplus(pred) * F.softplus(rs)
                head_out[res] = pred

            if len(head_out) == 1:
                result[head_name] = next(iter(head_out.values()))
            elif head_out:
                result[head_name] = head_out

        return result

    def eval(self):
        self.backbone.eval()
        return self

    def to(self, device):
        self.backbone.to(device)
        return self

    def __call__(self, *args, **kwargs):
        return self.backbone(*args, **kwargs)

    def predict_sequence(
        self,
        sequence: str,
        organism_index: int,
        heads: tuple,
        resolutions: tuple,
    ) -> dict:
        """Run predict() for a raw DNA sequence string."""
        import torch

        _BASE_ORDER_LOCAL = {"A": 0, "C": 1, "G": 2, "T": 3}
        n = len(sequence)
        t = torch.zeros(1, n, 4, dtype=torch.float32)
        for i, base in enumerate(sequence.upper()):
            idx = _BASE_ORDER_LOCAL.get(base)
            if idx is not None:
                t[0, i, idx] = 1.0
        dna = t.to(self.device)
        with torch.no_grad():
            return self.predict(dna, organism_index, heads, resolutions)

    def extract_track_signal(
        self,
        preds: dict,
        head: str,
        track_index: int,
        resolution: int,
    ) -> float:
        """Return the mean signal for one track from a predict() result."""
        import torch
        import torch.nn.functional as F

        head_out = preds.get(head)
        if head_out is None:
            raise KeyError(f"Head {head!r} not in predictions.")
        if isinstance(head_out, dict):
            tensor = head_out.get(resolution) or next(iter(head_out.values()))
        else:
            tensor = head_out

        arr = tensor.detach().cpu()
        if arr.ndim == 4:
            arr = arr[0]  # [L, L, T] for contact_maps
        if track_index >= arr.shape[-1]:
            raise IndexError(f"track_index {track_index} out of range for head {head!r} ({arr.shape[-1]} tracks).")
        return float(arr[..., track_index].mean())


def get_model():
    """Return the AlphaGenomePredictor, loading it on first call.

    Returns None if weights aren't present — callers should surface a clear
    error rather than blocking. Lazy loading keeps dev startup fast when only
    the metadata endpoints are being exercised.
    """
    global _model
    if _model is not None:
        return _model

    with _lock:
        if _model is not None:
            return _model

        settings = load_settings()
        weights_path: Path = settings.paths.alphagenome_weights
        if not weights_path.exists():
            logger.warning("AlphaGenome weights not found at %s — run scripts/download_model.sh", weights_path)
            return None

        from alphagenome_pytorch import AlphaGenome
        from safetensors.torch import load_file

        logger.info("Loading AlphaGenome from %s (device=%s)", weights_path, settings.alphagenome.device)
        raw_sd = load_file(str(weights_path))

        backbone = AlphaGenome()
        backbone_sd = _convert_official_to_community(raw_sd) if _is_official_format(raw_sd) else raw_sd
        if _is_official_format(raw_sd):
            logger.info("Detected official checkpoint format — converting backbone to alphagenome_pytorch layout")
        missing, unexpected = backbone.load_state_dict(backbone_sd, strict=False)
        if missing:
            logger.debug("load_state_dict missing keys (%d): %s …", len(missing), missing[:5])

        backbone = backbone.to(settings.alphagenome.device)
        backbone.eval()

        predictor = AlphaGenomePredictor(backbone, raw_sd, settings.alphagenome.device)
        _model = predictor
        logger.info("AlphaGenome loaded (backbone + %d head types).", len(predictor._heads))
        return _model
