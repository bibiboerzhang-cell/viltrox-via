// Verbatim from vkpi_v6.15.7_integrated.html


import { useEffect, useRef } from "react";
// @ts-ignore - 'three' ships no type declarations and @types/three is not installed; treat as any.
import * as THREE from "three";

export function Globe({ pins, accentColor, onPinClick, focusTarget }: any) {
  const containerRef = useRef<any>(null);
  const earthGroupRef = useRef<any>(null);
  const stateRef = useRef<any>({ autoRotate: true, isDragging: false, targetZoom: 3.2 });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const width = container.clientWidth;
    const height = container.clientHeight || 500;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.z = 3.2;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    renderer.setClearColor(0x000000, 0);
    container.appendChild(renderer.domElement);

    const earthGroup = new THREE.Group();
    earthGroupRef.current = earthGroup;
    scene.add(earthGroup);

    const earthGeometry = new THREE.SphereGeometry(1, 64, 64);
    const fallbackMaterial = new THREE.MeshPhongMaterial({
      color: 0x0a1a3a, emissive: 0x000814, specular: 0x111122, shininess: 10,
    });
    const earthMesh = new THREE.Mesh(earthGeometry, fallbackMaterial);
    earthGroup.add(earthMesh);

    const textureLoader = new THREE.TextureLoader();
    textureLoader.crossOrigin = "anonymous";
    const NIGHT_TEXTURE_URLS = [
      "https://unpkg.com/three-globe@2.31.1/example/img/earth-night.jpg",
      "https://cdn.jsdelivr.net/npm/three-globe@2.31.1/example/img/earth-night.jpg",
    ];
    let textureLoaded = false;
    NIGHT_TEXTURE_URLS.forEach((url) => {
      if (textureLoaded) return;
      textureLoader.load(url, (texture: any) => {
        if (textureLoaded) return;
        textureLoaded = true;
        texture.colorSpace = THREE.SRGBColorSpace;
        earthMesh.material = new THREE.MeshPhongMaterial({
          map: texture, emissive: 0x222244, emissiveIntensity: 0.6,
          specular: 0x223355, shininess: 8,
        });
      }, undefined, () => {});
    });

    const atmosphereGeometry = new THREE.SphereGeometry(1.08, 64, 64);
    const atmosphereMaterial = new THREE.ShaderMaterial({
      vertexShader: `varying vec3 vNormal; void main() { vNormal = normalize(normalMatrix * normal); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `varying vec3 vNormal; void main() { float intensity = pow(0.72 - dot(vNormal, vec3(0.0, 0.0, 1.0)), 3.0); gl_FragColor = vec4(0.35, 0.55, 1.0, 1.0) * intensity; }`,
      blending: THREE.AdditiveBlending, side: THREE.BackSide, transparent: true,
    });
    earthGroup.add(new THREE.Mesh(atmosphereGeometry, atmosphereMaterial));

    scene.add(new THREE.AmbientLight(0xffffff, 0.35));
    const sun = new THREE.DirectionalLight(0xffffff, 0.8);
    sun.position.set(-5, 3, 5);
    scene.add(sun);
    const rim = new THREE.DirectionalLight(0x4a7fff, 0.4);
    rim.position.set(5, 0, -5);
    scene.add(rim);

    const latLngToVec3 = (lat: any, lng: any, radius = 1) => {
      const phi = (90 - lat) * (Math.PI / 180);
      const theta = (lng + 180) * (Math.PI / 180);
      return new THREE.Vector3(
        -(radius * Math.sin(phi) * Math.cos(theta)),
        radius * Math.cos(phi),
        radius * Math.sin(phi) * Math.sin(theta)
      );
    };

    const pinsGroup = new THREE.Group();
    earthGroup.add(pinsGroup);

    const rebuildPins = (pinData: any, color: any) => {
      while (pinsGroup.children.length) {
        const child = pinsGroup.children[0];
        pinsGroup.remove(child);
        child.geometry?.dispose();
        child.material?.dispose();
      }

      const pinObjects: any[] = [];
      pinData.forEach((p: any, idx: number) => {
        const pos = latLngToVec3(p.lat, p.lng, 1.015);
        const pinColor = new THREE.Color(p.color || color);
        const sizeScale = p.scale || 1;

        const pinGeometry = new THREE.SphereGeometry(0.012 * sizeScale, 16, 16);
        const pinMaterial = new THREE.MeshBasicMaterial({ color: 0xe0f0ff });
        const pin = new THREE.Mesh(pinGeometry, pinMaterial);
        pin.position.copy(pos);
        pin.userData = { pin: p, idx };
        pinsGroup.add(pin);

        const haloGeometry = new THREE.RingGeometry(0.025 * sizeScale, 0.04 * sizeScale, 32);
        const haloMaterial = new THREE.MeshBasicMaterial({
          color: pinColor, transparent: true, opacity: 0.5, side: THREE.DoubleSide,
        });
        const halo = new THREE.Mesh(haloGeometry, haloMaterial);
        halo.position.copy(pos);
        halo.lookAt(0, 0, 0);
        halo.rotateY(Math.PI);
        pinsGroup.add(halo);

        const pulseGeometry = new THREE.RingGeometry(0.04 * sizeScale, 0.06 * sizeScale, 32);
        const pulseMaterial = new THREE.MeshBasicMaterial({
          color: pinColor, transparent: true, opacity: 0.35, side: THREE.DoubleSide,
        });
        const pulse = new THREE.Mesh(pulseGeometry, pulseMaterial);
        pulse.position.copy(pos);
        pulse.lookAt(0, 0, 0);
        pulse.rotateY(Math.PI);
        pinsGroup.add(pulse);

        const beamHeight = 0.08 * sizeScale;
        const beamGeometry = new THREE.ConeGeometry(0.015 * sizeScale, beamHeight, 12, 1, true);
        const beamMaterial = new THREE.MeshBasicMaterial({
          color: pinColor, transparent: true, opacity: 0.6, side: THREE.DoubleSide,
        });
        const beam = new THREE.Mesh(beamGeometry, beamMaterial);
        const beamPos = latLngToVec3(p.lat, p.lng, 1.05);
        beam.position.copy(beamPos);
        beam.lookAt(0, 0, 0);
        beam.rotateX(Math.PI / 2);
        pinsGroup.add(beam);

        pinObjects.push({ pin, halo, pulse, beam, phase: idx * 0.6 });
      });
      return pinObjects;
    };

    let pinObjects = rebuildPins(pins, accentColor);
    container._rebuildPins = (newPins: any, newColor: any) => {
      pinObjects = rebuildPins(newPins, newColor);
    };

    container._focusOn = (lat: any, lng: any, zoom = 2.0) => {
      stateRef.current.autoRotate = false;
      const phi = (90 - lat) * (Math.PI / 180);
      const theta = (lng + 180) * (Math.PI / 180);
      const targetY = -theta + Math.PI / 2;
      const targetX = -((Math.PI / 2) - phi);

      const startY = earthGroup.rotation.y;
      const startX = earthGroup.rotation.x;
      const startZoom = camera.position.z;
      const t0 = performance.now();
      const dur = 1400;
      const ease = (t: any) => 1 - Math.pow(1 - t, 3);

      const animateFocus = () => {
        const t = Math.min(1, (performance.now() - t0) / dur);
        const k = ease(t);
        earthGroup.rotation.y = startY + (targetY - startY) * k;
        earthGroup.rotation.x = startX + (targetX - startX) * k;
        camera.position.z = startZoom + (zoom - startZoom) * k;
        stateRef.current.targetZoom = zoom;
        if (t < 1) requestAnimationFrame(animateFocus);
      };
      requestAnimationFrame(animateFocus);
    };

    container._resetView = () => {
      stateRef.current.autoRotate = true;
      stateRef.current.targetZoom = 3.2;
      const t0 = performance.now();
      const startX = earthGroup.rotation.x;
      const dur = 800;
      const animateReset = () => {
        const t = Math.min(1, (performance.now() - t0) / dur);
        const k = 1 - Math.pow(1 - t, 3);
        earthGroup.rotation.x = startX * (1 - k);
        if (t < 1) requestAnimationFrame(animateReset);
      };
      requestAnimationFrame(animateReset);
    };

    let prevMouse = { x: 0, y: 0 };
    let rotationVelocity = { x: 0, y: 0 };

    const onMouseDown = (ev: any) => {
      stateRef.current.isDragging = true;
      stateRef.current.autoRotate = false;
      prevMouse = { x: ev.clientX, y: ev.clientY };
      container.style.cursor = "grabbing";
    };
    const onMouseMove = (ev: any) => {
      if (!stateRef.current.isDragging) return;
      const dx = (ev.clientX - prevMouse.x) * 0.005;
      const dy = (ev.clientY - prevMouse.y) * 0.005;
      earthGroup.rotation.y += dx;
      earthGroup.rotation.x = Math.max(-Math.PI / 2 + 0.3, Math.min(Math.PI / 2 - 0.3, earthGroup.rotation.x + dy));
      rotationVelocity = { x: dy, y: dx };
      prevMouse = { x: ev.clientX, y: ev.clientY };
    };
    const onMouseUp = () => {
      stateRef.current.isDragging = false;
      container.style.cursor = "grab";
    };
    const onWheel = (ev: any) => {
      ev.preventDefault();
      stateRef.current.targetZoom = Math.max(1.08, Math.min(6, stateRef.current.targetZoom + ev.deltaY * 0.003));
    };
    const onClick = (ev: any) => {
      const rect = renderer.domElement.getBoundingClientRect();
      const mouse = new THREE.Vector2(
        ((ev.clientX - rect.left) / rect.width) * 2 - 1,
        -((ev.clientY - rect.top) / rect.height) * 2 + 1
      );
      const raycaster = new THREE.Raycaster();
      raycaster.setFromCamera(mouse, camera);
      const hits = raycaster.intersectObjects(pinsGroup.children, false);
      if (hits.length > 0 && hits[0].object.userData?.pin) {
        if (onPinClick) onPinClick(hits[0].object.userData.pin);
      }
    };

    container.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    container.addEventListener("wheel", onWheel, { passive: false });
    container.addEventListener("click", onClick);
    container.style.cursor = "grab";

    const onTouchStart = (ev: any) => {
      if (ev.touches.length === 1) {
        stateRef.current.isDragging = true;
        stateRef.current.autoRotate = false;
        prevMouse = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
      }
    };
    const onTouchMove = (ev: any) => {
      if (!stateRef.current.isDragging || ev.touches.length !== 1) return;
      const dx = (ev.touches[0].clientX - prevMouse.x) * 0.005;
      const dy = (ev.touches[0].clientY - prevMouse.y) * 0.005;
      earthGroup.rotation.y += dx;
      earthGroup.rotation.x = Math.max(-Math.PI / 2 + 0.3, Math.min(Math.PI / 2 - 0.3, earthGroup.rotation.x + dy));
      prevMouse = { x: ev.touches[0].clientX, y: ev.touches[0].clientY };
    };
    const onTouchEnd = () => { stateRef.current.isDragging = false; };
    container.addEventListener("touchstart", onTouchStart, { passive: true });
    container.addEventListener("touchmove", onTouchMove, { passive: true });
    container.addEventListener("touchend", onTouchEnd);

    earthGroup.rotation.y = -Math.PI / 4;

    let animationId: any;
    const clock = new THREE.Clock();
    const animateLoop = () => {
      animationId = requestAnimationFrame(animateLoop);
      const elapsed = clock.getElapsedTime();

      if (stateRef.current.autoRotate && !stateRef.current.isDragging) {
        earthGroup.rotation.y += 0.0008;
      }
      if (!stateRef.current.isDragging) {
        earthGroup.rotation.y += rotationVelocity.y * 0.95;
        rotationVelocity.y *= 0.94;
        if (Math.abs(rotationVelocity.y) < 0.0001) rotationVelocity.y = 0;
      }
      camera.position.z += (stateRef.current.targetZoom - camera.position.z) * 0.1;

      pinObjects.forEach(({ pin, pulse, halo, beam, phase }) => {
        const t = (Math.sin(elapsed * 2 + phase) + 1) / 2;
        const scale = 1 + t * 1.2;
        pulse.scale.set(scale, scale, scale);
        pulse.material.opacity = 0.35 * (1 - t);
        halo.scale.set(1 + t * 0.3, 1 + t * 0.3, 1);
        halo.material.opacity = 0.5 + t * 0.3;
        const pinScale = 1 + t * 0.4;
        pin.scale.set(pinScale, pinScale, pinScale);
        beam.material.opacity = 0.4 + t * 0.4;
      });

      renderer.render(scene, camera);
    };
    animateLoop();

    const handleResize = () => {
      const w = container.clientWidth;
      const h = container.clientHeight || 500;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", handleResize);
    const resizeObserver = new ResizeObserver(handleResize);
    resizeObserver.observe(container);

    return () => {
      cancelAnimationFrame(animationId);
      container.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      container.removeEventListener("wheel", onWheel);
      container.removeEventListener("click", onClick);
      container.removeEventListener("touchstart", onTouchStart);
      container.removeEventListener("touchmove", onTouchMove);
      container.removeEventListener("touchend", onTouchEnd);
      window.removeEventListener("resize", handleResize);
      resizeObserver.disconnect();
      renderer.dispose();
      earthGeometry.dispose();
      atmosphereGeometry.dispose();
      atmosphereMaterial.dispose();
      if (renderer.domElement.parentNode === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []);

  useEffect(() => {
    if (containerRef.current && containerRef.current._rebuildPins) {
      containerRef.current._rebuildPins(pins, accentColor);
    }
  }, [pins, accentColor]);

  useEffect(() => {
    if (focusTarget && containerRef.current && containerRef.current._focusOn) {
      containerRef.current._focusOn(focusTarget.lat, focusTarget.lng, focusTarget.zoom || 2.0);
    } else if (!focusTarget && containerRef.current && containerRef.current._resetView) {
      containerRef.current._resetView();
    }
  }, [focusTarget]);

  return <div ref={containerRef} className="absolute inset-0" style={{ touchAction: "none" }} />;
}
