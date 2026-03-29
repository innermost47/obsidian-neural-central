import numpy as np


def applicate_lite_fade_in_fade_out(audio, sr):
    fade_ms = 5
    fade_samples = int(sr * (fade_ms / 1000.0))

    is_stereo = audio.ndim == 2 and audio.shape[0] == 2

    if is_stereo:
        num_samples = audio.shape[1]

        if num_samples > 2 * fade_samples:
            fade_out_ramp = np.linspace(1.0, 0.0, fade_samples)
            fade_in_ramp = np.linspace(0.0, 1.0, fade_samples)

            for channel in range(2):
                end_part = audio[channel, -fade_samples:]
                start_part = audio[channel, :fade_samples]

                audio[channel, :fade_samples] = (
                    start_part * fade_in_ramp + end_part * fade_out_ramp
                )

                audio[channel, -fade_samples:] = end_part * fade_out_ramp
        else:
            print(f"ℹ️  Audio too short for {fade_ms}ms crossfade (stereo).")

    else:
        num_samples = len(audio)

        if num_samples > 2 * fade_samples:
            end_part = audio[-fade_samples:]
            start_part = audio[:fade_samples]

            fade_out_ramp = np.linspace(1.0, 0.0, fade_samples)
            fade_in_ramp = np.linspace(0.0, 1.0, fade_samples)

            audio[:fade_samples] = start_part * fade_in_ramp + end_part * fade_out_ramp
            audio[-fade_samples:] = end_part * fade_out_ramp
        else:
            print(f"ℹ️  Audio too short for {fade_ms}ms crossfade (mono).")

    return audio
