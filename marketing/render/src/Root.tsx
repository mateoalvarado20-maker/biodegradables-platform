import React from 'react';
import {Composition} from 'remotion';
import {Carousel, CarouselProps} from './Carousel';
import {VerticalVideo, VideoProps, FPS} from './VerticalVideo';

const defaultProps: VideoProps = {
  title: 'Título de ejemplo',
  hook: 'Hook de ejemplo',
  brand_color: '#1B7A43',
  scenes: [],
};

const defaultCarousel: CarouselProps = {
  brand_color: '#1B7A43',
  brand_name: 'Marca',
  cta: 'Escríbenos',
  slides: [{title: 'Slide', body: 'Cuerpo'}],
};

export const Root: React.FC = () => {
  return (
    <>
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
    <Composition
      id="CarouselSlide"
      component={Carousel}
      width={1080}
      height={1920}
      fps={1}
      durationInFrames={1}
      defaultProps={defaultCarousel}
      calculateMetadata={({props}) => ({
        durationInFrames: Math.max(props.slides.length, 1),
      })}
    />
    </>
  );
};
