/* A real Google map underneath the dashboard's Leaflet layer.
 *
 * Why it is built this way
 * ------------------------
 * Google's tiles may only be drawn by Google's own library. They cannot be
 * fetched as XYZ tiles and dropped into `L.tileLayer` - that is against the
 * terms and the URLs are not public API. So the geometry stays where it is,
 * in Leaflet, and Google draws the ground underneath it.
 *
 * It lives INSIDE Leaflet's map pane
 * ----------------------------------
 * The first version put the Google canvas in the map container and copied the
 * centre across on every `move`. That lags: Leaflet pans by translating one
 * pane, which is instant, while `setCenter` goes through Google's own camera
 * and arrives a frame or two later. The field boxes therefore slid across the
 * imagery while the farmer dragged - which reads as the plot boxes having come
 * loose from the ground, not as a basemap being slow.
 *
 * So the canvas is parented into `.leaflet-map-pane` instead, beneath the tile
 * pane. Leaflet's drag transform now moves the ground and the boxes together,
 * as one object, because they are in the same pane - there is nothing left to
 * synchronise while the drag is happening. Google is only re-centred once the
 * movement has ENDED, and at the same moment the canvas is offset back to the
 * viewport by the pane's running translation.
 *
 * Both maps agree on the projection - Web Mercator, 256-pixel tiles, the same
 * zoom numbering - so the re-centre is exact rather than approximate. Leaflet's
 * fractional zoom is off by default on these maps, which is what keeps it that
 * way.
 *
 * What happens when Google will not load
 * --------------------------------------
 * A missing key, a key whose HTTP-referrer allowlist does not include this
 * hostname, or a Cloud project with billing switched off all end the same way:
 * no map. There is no tile fallback any more, so the caller is told and says so
 * on screen rather than leaving a blank rectangle.
 */
'use strict';

(function () {
  const STYLE_ID = 'saarthiGmapStyle';
  let keyPromise = null;
  let libraryPromise = null;

  /** The key, asked for once and remembered. */
  function mapsKey() {
    if (keyPromise) return keyPromise;
    keyPromise = fetch('/api/maps', { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((body) => (body && body.key) || '')
      .catch(() => '');
    return keyPromise;
  }

  /** Load the Maps library once, however many maps ask for it. */
  function library() {
    if (window.google && window.google.maps && window.google.maps.Map) {
      return Promise.resolve(true);
    }
    if (libraryPromise) return libraryPromise;
    libraryPromise = mapsKey().then((key) => {
      if (!key) throw new Error('No Google Maps key is configured.');
      return new Promise((resolve, reject) => {
        const tag = document.createElement('script');
        tag.src = 'https://maps.googleapis.com/maps/api/js?key='
          + encodeURIComponent(key) + '&loading=async&v=quarterly';
        tag.async = true;
        tag.onload = () => resolve(true);
        tag.onerror = () => {
          libraryPromise = null;              // let a later attempt retry
          reject(new Error('Google Maps could not be loaded.'));
        };
        document.head.appendChild(tag);
      });
    }).catch((err) => {
      /* Forget the failure as well as the success. Holding a rejected promise
         here is why a map that failed once stayed failed for the whole visit,
         even after the farmer navigated to a page that would have worked. */
      libraryPromise = null;
      throw err;
    });
    return libraryPromise;
  }

  function styleOnce() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      /* Beneath every Leaflet pane, and not catching the pointer: Leaflet must
         keep receiving the drags, the clicks that place a plot and the scroll
         wheel. Google is a picture here, not a control surface. */
      '.saarthi-gmap{position:absolute;left:0;top:0;z-index:0;pointer-events:none}',
      '.leaflet-container .leaflet-tile-pane{background:transparent}',
      /* ...except for anything Google puts ON TOP of the picture to talk to
         the reader. Its "this page can't load Google Maps correctly" dialog is
         drawn inside this canvas, so `pointer-events:none` on the parent left
         an OK button that could not be pressed. These few put the pointer back
         for the message itself without giving the map any of the gestures. */
      '.saarthi-gmap .gm-style-moc,',
      '.saarthi-gmap .gm-err-container,',
      '.saarthi-gmap .gm-err-content,',
      '.saarthi-gmap [role="alertdialog"],',
      '.saarthi-gmap [role="dialog"],',
      '.saarthi-gmap .dismissButton{pointer-events:auto}',
    ].join('');
    document.head.appendChild(style);
  }

  /**
   * Put a Google basemap under one Leaflet map and keep it in step.
   *
   * Resolves true when Google is drawing and false when it is not; it never
   * throws, because a map that cannot load its ground is a worse map, not a
   * broken page.
   */
  function attach(leafletMap) {
    if (!leafletMap || leafletMap.__saarthiGmap) return Promise.resolve(true);

    return library().then(() => {
      styleOnce();
      const L = window.L;
      /* The pane Leaflet translates when the farmer drags. Everything inside
         it moves as one, which is the whole point of putting the canvas here. */
      const pane = leafletMap.getPane('mapPane')
        || leafletMap.getContainer().querySelector('.leaflet-map-pane');
      if (!pane) throw new Error('Leaflet has no map pane to draw under.');

      const box = document.createElement('div');
      box.className = 'saarthi-gmap';
      pane.insertBefore(box, pane.firstChild);

      const centre = leafletMap.getCenter();
      const gmap = new window.google.maps.Map(box, {
        center: { lat: centre.lat, lng: centre.lng },
        zoom: Math.round(leafletMap.getZoom()),
        /* Every control is Leaflet's. Two sets of zoom buttons on one map is
           two things that disagree about what the zoom is. */
        disableDefaultUI: true,
        keyboardShortcuts: false,
        gestureHandling: 'none',
        /* Imagery with Google's own place names on it - the view a farmer
           looking for their own field actually needs, and the one thing no
           other provider will composite for you. */
        mapTypeId: 'hybrid',
        tilt: 0,
      });

      /* Re-seat the canvas and re-centre Google. Called when a movement has
         FINISHED, never during one: mid-drag the pane's transform is already
         carrying the canvas, and asking Google to move as well would fight it. */
      const settle = () => {
        const size = leafletMap.getSize();
        box.style.width = size.x + 'px';
        box.style.height = size.y + 'px';
        /* The map pane holds a running translation - Leaflet does not reset it
           after a drag - so the canvas is pushed back by exactly that much to
           sit over the viewport again. */
        const offset = L.DomUtil.getPosition(pane) || { x: 0, y: 0 };
        box.style.left = (-offset.x) + 'px';
        box.style.top = (-offset.y) + 'px';

        const middle = leafletMap.getCenter();
        gmap.setZoom(Math.round(leafletMap.getZoom()));
        gmap.setCenter({ lat: middle.lat, lng: middle.lng });
        window.google.maps.event.trigger(gmap, 'resize');
      };

      leafletMap.on('moveend zoomend resize viewreset load', settle);
      settle();
      /* Once more after the pane has been laid out. A map created on a panel
         that is still being shown has no size yet, and a Google canvas built at
         zero by zero draws nothing until something tells it otherwise - which
         is one of the ways the map "sometimes" did not appear. */
      window.setTimeout(settle, 250);
      window.setTimeout(settle, 1200);

      leafletMap.__saarthiGmap = { gmap, box, settle };
      return true;
    }).catch((err) => {
      /* Said once, quietly, with the reason. The causes are a missing key, a
         referrer allowlist that does not include this hostname, and a Cloud
         project with billing off - all fixed in the Google console, not here. */
      if (!window.__saarthiGmapWarned) {
        window.__saarthiGmapWarned = true;
        console.info('Google basemap unavailable: ' + err.message);
      }
      return false;
    });
  }

  /** Is Google actually drawing under this map? */
  function attached(leafletMap) {
    return Boolean(leafletMap && leafletMap.__saarthiGmap);
  }

  window.SaarthiGoogleMap = { attach, attached, key: mapsKey };
})();
