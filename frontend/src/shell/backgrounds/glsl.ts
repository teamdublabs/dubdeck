// WebGL background scenes — each is a fullscreen fragment shader (GLSL ES 1.00).
// No three.js: one passthrough vertex shader + a swappable fragment shader.

export const VERT = `
attribute vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`

// Shared header: uniforms, noise toolkit, smooth round stars, ACES tonemap.
const HEAD = `
precision highp float;
uniform float u_time;
uniform vec2  u_res;
uniform vec2  u_mouse;

float hash21(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
mat2 rot(float a){ float c=cos(a), s=sin(a); return mat2(c,-s,s,c); }
float hash13(vec3 p){ p = fract(p*0.3183099+0.1); p *= 17.0; return fract(p.x*p.y*p.z*(p.x+p.y+p.z)); }
float noise(vec3 x){
  vec3 i = floor(x); vec3 f = fract(x); f = f*f*(3.0-2.0*f);
  return mix(mix(mix(hash13(i+vec3(0,0,0)),hash13(i+vec3(1,0,0)),f.x),
                 mix(hash13(i+vec3(0,1,0)),hash13(i+vec3(1,1,0)),f.x),f.y),
             mix(mix(hash13(i+vec3(0,0,1)),hash13(i+vec3(1,0,1)),f.x),
                 mix(hash13(i+vec3(0,1,1)),hash13(i+vec3(1,1,1)),f.x),f.y), f.z);
}
float fbm(vec3 p){
  float v=0.0, a=0.5;
  for(int i=0;i<6;i++){ v += a*noise(p); p = p*2.02 + 11.0; a *= 0.5; }
  return v;
}
// smooth round stars: one twinkling point placed randomly inside each cell
float stars(vec2 uv, float density){
  vec2 g = uv*density;
  vec2 id = floor(g);
  vec2 f = fract(g) - 0.5;
  float h = hash21(id);
  if(h < 0.86) return 0.0;                         // sparse
  vec2 off = (vec2(hash21(id+1.7), hash21(id+4.3)) - 0.5) * 0.7;
  float d = length(f - off);
  float tw = 0.55 + 0.45*sin(u_time*2.2 + h*52.0);
  return pow(smoothstep(0.42, 0.0, d), 6.0) * tw;
}
vec3 aces(vec3 x){ return clamp((x*(2.51*x+0.03))/(x*(2.43*x+0.59)+0.14), 0.0, 1.0); }
`

// Liquid chrome / dark iridescent
const CHROME = `${HEAD}
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*u_res) / u_res.y;
  uv += u_mouse * 0.025;
  float t = u_time * 0.07;

  vec2 q = uv*1.4;
  for(int i=0;i<4;i++){
    q += 0.5*(vec2(fbm(vec3(q + t, 1.0)), fbm(vec3(q.yx - t, 4.0))) - 0.5);
  }
  float h = fbm(vec3(q*1.3, t));
  // finite-difference gradient (no dFdx/dFdy — those need a WebGL1 extension)
  float e = 0.012;
  float hx = fbm(vec3((q + vec2(e,0.0))*1.3, t));
  float hy = fbm(vec3((q + vec2(0.0,e))*1.3, t));
  float grad = length(vec2(hx - h, hy - h)) / e;             // ridge edges => specular
  vec3 sheen = 0.5 + 0.5*cos(h*6.28318*1.7 + vec3(0.0, 2.1, 4.2));

  vec3 col = mix(vec3(0.015,0.018,0.03), sheen, 0.38);       // dark metal base
  col += sheen * pow(clamp(grad*0.5, 0.0, 1.5), 1.4) * 0.9;  // bright flowing highlights
  col *= 0.8;
  col = aces(col);
  col *= smoothstep(1.8, 0.3, length(uv));
  gl_FragColor = vec4(col, 1.0);
}`

// Energy flow-field currents
const FLOW = `${HEAD}
void main(){
  vec2 uv = (gl_FragCoord.xy - 0.5*u_res) / u_res.y;
  uv += u_mouse * 0.05;
  float t = u_time * 0.12;

  vec2 q = uv*1.8;
  for(int i=0;i<4;i++){
    vec2 w = vec2(fbm(vec3(q + t, 0.0)), fbm(vec3(q.yx - t*0.8, 9.0)));
    q += 0.55*(w - 0.5);
  }
  float flow = fbm(vec3(q*1.6, t*1.5));
  float fil  = pow(1.0 - abs(flow*2.0 - 1.0), 9.0);          // glowing filaments
  float fine = pow(1.0 - abs(fbm(vec3(q*4.0, t*2.0))*2.0 - 1.0), 13.0);
  float energy = fil*1.3 + fine*0.8;

  vec3 cA = vec3(0.10,0.95,1.0);
  vec3 cB = vec3(0.45,0.28,1.0);
  vec3 cC = vec3(0.0,1.0,0.55);
  vec3 tint = mix(cA, cB, smoothstep(0.2,0.8,flow));
  tint = mix(tint, cC, smoothstep(0.5,0.9, fbm(vec3(q*1.2, t))));

  vec3 col = tint * energy;
  col += cA * pow(fil, 2.0) * 0.12;
  col = aces(col);
  col *= smoothstep(1.5, 0.25, length(uv));
  gl_FragColor = vec4(col, 1.0);
}`

// Synthwave neon grid horizon
const GRID = `${HEAD}
void main(){
  vec2 p = (gl_FragCoord.xy - 0.5*u_res) / u_res.y;
  vec3 col;

  if(p.y > 0.0){
    col = mix(vec3(0.55,0.10,0.55), vec3(0.03,0.01,0.10), smoothstep(0.0, 0.65, p.y));
    float sd = length((p - vec2(0.0,0.10)) * vec2(1.0,1.1));
    float sun = smoothstep(0.30, 0.285, sd);
    float bands = step(0.0, sin((p.y-0.10)*70.0 - 1.5));
    vec3 sunCol = mix(vec3(1.0,0.85,0.25), vec3(1.0,0.20,0.55), smoothstep(-0.1,0.35,p.y));
    col = mix(col, sunCol, sun*bands);
    col += vec3(stars(p + 5.0, 24.0)) * smoothstep(0.2, 0.8, p.y);
  } else {
    float fy = -p.y;
    vec2 g;
    g.x = p.x / (fy + 0.04);
    g.y = 1.0 / (fy + 0.04) + u_time*0.9;
    g *= 1.5;
    vec2 f = abs(fract(g) - 0.5);
    float gx = 1.0 - smoothstep(0.0, 0.055, f.x);
    float gy = 1.0 - smoothstep(0.0, 0.055, f.y);
    float grid = max(gx, gy) * smoothstep(0.0, 0.18, fy);
    col = vec3(0.02,0.0,0.05) + vec3(0.0,0.95,1.0) * grid * 0.9 * exp(-fy*1.2);
  }

  float hg = smoothstep(0.05, 0.0, abs(p.y));
  col += vec3(1.0,0.3,0.7) * hg * 0.7;
  col += vec3(0.0,0.9,1.0) * hg * 0.3;
  gl_FragColor = vec4(col, 1.0);
}`

// Flat black — no animation; marked static so the render loop draws once and stops.
const BLACK = `
precision highp float;
void main(){ gl_FragColor = vec4(0.0, 0.0, 0.0, 1.0); }`

export interface ThemeDef { id: string; label: string; frag: string; static?: boolean }

export const DEFAULT_THEME = "black"

export const THEMES: ThemeDef[] = [
  { id: "black", label: "Flat Black", frag: BLACK, static: true },
  { id: "flow", label: "Energy Flow", frag: FLOW },
  { id: "chrome", label: "Liquid Chrome", frag: CHROME },
  { id: "grid", label: "Synthwave Grid", frag: GRID },
]

export const SHADERS: Record<string, ThemeDef> = Object.fromEntries(
  THEMES.map((t) => [t.id, t]),
)
