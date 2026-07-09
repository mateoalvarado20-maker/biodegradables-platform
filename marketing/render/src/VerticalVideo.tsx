import React from 'react';
import {
  AbsoluteFill,
  Audio,
  Loop,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export const FPS = 30;

export type Word = {word: string; start_ms: number; end_ms: number};

export type SceneProps = {
  audio: string; // ruta relativa a public/
  video: string; // ruta relativa a public/
  duration_ms: number;
  video_duration_ms: number | null; // duración del clip b-roll (para Loop)
  words: Word[];
};

export type VideoProps = {
  title: string;
  hook: string;
  brand_color: string;
  scenes: SceneProps[];
};

const WORDS_PER_PAGE = 3;

// Subtítulos karaoke: páginas de 3 palabras, la palabra hablada resalta.
// Los timestamps vienen de los word boundaries del TTS (sync exacto, F1.3).
const Captions: React.FC<{words: Word[]; brandColor: string}> = ({words, brandColor}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const tMs = (frame / fps) * 1000;

  const pages: Word[][] = [];
  for (let i = 0; i < words.length; i += WORDS_PER_PAGE) {
    pages.push(words.slice(i, i + WORDS_PER_PAGE));
  }
  const page = pages.find(
    (p) => tMs >= p[0].start_ms - 120 && tMs <= p[p.length - 1].end_ms + 200,
  );
  if (!page) return null;

  return (
    <AbsoluteFill style={{justifyContent: 'flex-end', alignItems: 'center'}}>
      <div
        style={{
          marginBottom: 480,
          padding: '18px 36px',
          borderRadius: 24,
          backgroundColor: 'rgba(0,0,0,0.55)',
          maxWidth: 920,
          textAlign: 'center',
        }}
      >
        {page.map((w, i) => {
          const active = tMs >= w.start_ms && tMs <= w.end_ms + 60;
          return (
            <span
              key={`${w.start_ms}-${i}`}
              style={{
                fontFamily: 'Arial Black, Arial, sans-serif',
                fontWeight: 900,
                fontSize: 68,
                lineHeight: 1.25,
                marginRight: 18,
                color: active ? brandColor : 'white',
                textShadow: '0 4px 12px rgba(0,0,0,0.9)',
                transform: active ? 'scale(1.08)' : 'scale(1)',
                display: 'inline-block',
                transition: 'transform 60ms',
              }}
            >
              {w.word}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// Overlay del hook, solo durante la primera escena.
const HookOverlay: React.FC<{hook: string; brandColor: string}> = ({hook, brandColor}) => (
  <AbsoluteFill style={{justifyContent: 'flex-start', alignItems: 'center'}}>
    <div
      style={{
        marginTop: 280,
        padding: '22px 44px',
        borderRadius: 20,
        backgroundColor: brandColor,
        maxWidth: 940,
        textAlign: 'center',
        fontFamily: 'Arial Black, Arial, sans-serif',
        fontWeight: 900,
        fontSize: 60,
        lineHeight: 1.2,
        color: 'white',
        boxShadow: '0 8px 28px rgba(0,0,0,0.45)',
      }}
    >
      {hook}
    </div>
  </AbsoluteFill>
);

// OffthreadVideo decodifica con ffmpeg (no con el <video> del browser) —
// elimina los delayRender timeouts que hicieron fallar 3x la pieza 4 del
// lote F1.8. No soporta `loop`, así que cuando el clip es más corto que la
// escena lo envolvemos en <Loop> con su duración real (viene de la API).
const BRoll: React.FC<{scene: SceneProps}> = ({scene}) => {
  const clip = (
    <OffthreadVideo
      src={staticFile(scene.video)}
      muted
      style={{width: '100%', height: '100%', objectFit: 'cover'}}
    />
  );
  const clipMs = scene.video_duration_ms;
  if (clipMs && clipMs < scene.duration_ms) {
    const frames = Math.max(Math.floor((clipMs / 1000) * FPS) - 1, 1);
    return <Loop durationInFrames={frames}>{clip}</Loop>;
  }
  return clip;
};

const Scene: React.FC<{
  scene: SceneProps;
  isFirst: boolean;
  hook: string;
  brandColor: string;
}> = ({scene, isFirst, hook, brandColor}) => {
  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      <BRoll scene={scene} />
      <Audio src={staticFile(scene.audio)} />
      {isFirst ? <HookOverlay hook={hook} brandColor={brandColor} /> : null}
      <Captions words={scene.words} brandColor={brandColor} />
    </AbsoluteFill>
  );
};

export const VerticalVideo: React.FC<VideoProps> = ({hook, brand_color, scenes}) => {
  let fromFrame = 0;
  return (
    <AbsoluteFill style={{backgroundColor: 'black'}}>
      {scenes.map((scene, i) => {
        const durationInFrames = Math.max(Math.round((scene.duration_ms / 1000) * FPS), 1);
        const seq = (
          <Sequence key={i} from={fromFrame} durationInFrames={durationInFrames}>
            <Scene scene={scene} isFirst={i === 0} hook={hook} brandColor={brand_color} />
          </Sequence>
        );
        fromFrame += durationInFrames;
        return seq;
      })}
    </AbsoluteFill>
  );
};
