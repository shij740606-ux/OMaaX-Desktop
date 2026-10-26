const interactive = 'button,a,input,select,textarea,summary,[role="button"],[role="tab"],[contenteditable]:not([contenteditable="false"])';

/** Tauri's deep region includes title text; controls and explicit exclusions remain clickable. */
export function installWindowDragRegions() {
  const managed = new Map<Element, string>();
  const mark = () => {
    const regions = new Map<Element, string>();
    document.querySelectorAll('.drag').forEach(region => {
      regions.set(region, 'deep');
      region.querySelectorAll(interactive).forEach(control => regions.set(control, 'false'));
    });
    document.querySelectorAll('.no-drag').forEach(region => regions.set(region, 'false'));
    for (const [element, value] of managed) {
      if (!regions.has(element)) {
        if (element.getAttribute('data-tauri-drag-region') === value) element.removeAttribute('data-tauri-drag-region');
        managed.delete(element);
      }
    }
    for (const [element, value] of regions) {
      if (!managed.has(element) && element.getAttribute('data-tauri-drag-region') === 'false') continue;
      if (element.getAttribute('data-tauri-drag-region') !== value) element.setAttribute('data-tauri-drag-region', value);
      managed.set(element, value);
    }
  };
  mark();
  const observer = new MutationObserver(mark);
  observer.observe(document.documentElement, {childList:true, subtree:true, attributes:true, attributeFilter:['class']});
  return () => observer.disconnect();
}
