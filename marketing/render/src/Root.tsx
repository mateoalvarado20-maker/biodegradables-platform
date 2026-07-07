import React from 'react';
import {Composition} from 'remotion';
import {VerticalVideo, VideoProps, FPS} from './VerticalVideo';

const defaultProps: VideoProps = {
  title: 'Título de ejemplo',
  hook: 'Hook de ejemplo',
  brand_color: '#1B7A43',
  scenes: [],
};

export const Root: React.FC = () => {
  return (
    <Composition
      id="VerticalVideo"
      component={VerticalVideo}
      width={1080}
      height={1920}
      fps={FPS}
      durationInFrames={FPS * 5}
      defaultProps={defaultProps}
      calculateMetadata={({props}) => {
        const totalMs = props.scenes.reduce((acc, s) => acc + s.duration_ms, 0);
        return {
          durationInFrames: Math.max(Math.round((totalMs / 1000) * FPS), FPS),
        };
      }}
    />
  );
};
