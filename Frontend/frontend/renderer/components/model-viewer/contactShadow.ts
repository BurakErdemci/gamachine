import * as THREE from 'three';

/**
 * A soft blob on the ground under the mannequin: a quad whose shader fades a
 * radial falloff, so it needs no shadow maps and no canvas texture (jsdom has
 * no 2D canvas). It follows `follow`'s world X/Z every time it is drawn, so a
 * clip with root motion keeps its shadow.
 */

export const CONTACT_SHADOW_NAME = 'mannequin:contactShadow';

const OPACITY = 0.4;

export const createContactShadow = (
  radius: number,
  groundY: number,
  follow: THREE.Object3D | null,
): THREE.Mesh => {
  const material = new THREE.ShaderMaterial({
    uniforms: { opacity: { value: OPACITY }, color: { value: new THREE.Color(0x000000) } },
    vertexShader: `
      varying vec2 vUv;
      void main() {
        vUv = uv * 2.0 - 1.0;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }`,
    fragmentShader: `
      uniform float opacity;
      uniform vec3 color;
      varying vec2 vUv;
      void main() {
        float d = clamp(length(vUv), 0.0, 1.0);
        float a = 1.0 - smoothstep(0.0, 1.0, d);
        gl_FragColor = vec4(color, opacity * a * a);
      }`,
    transparent: true,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), material);
  mesh.name = CONTACT_SHADOW_NAME;
  mesh.rotation.x = -Math.PI / 2;
  mesh.scale.setScalar(radius);
  // A hair above the grid so the two never z-fight.
  mesh.position.y = groundY + radius * 0.002;
  mesh.frustumCulled = false;
  if (follow) {
    const at = new THREE.Vector3();
    mesh.onBeforeRender = () => {
      follow.getWorldPosition(at);
      if (mesh.parent) mesh.parent.worldToLocal(at);
      mesh.position.x = at.x;
      mesh.position.z = at.z;
      mesh.updateMatrixWorld();
    };
  }
  return mesh;
};

/** Recolour every contact shadow under `root` (theme switch). */
export const tintContactShadows = (root: THREE.Object3D, color: string): void => {
  root.traverse(o => {
    if (o.name !== CONTACT_SHADOW_NAME) return;
    ((o as THREE.Mesh).material as THREE.ShaderMaterial).uniforms.color.value.set(color);
  });
};
