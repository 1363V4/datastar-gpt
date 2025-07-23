const shrinkAnimation = anime.animate('#wp-icon', {
    scale: [1, 0.9, 1],
    duration: 100,
    easing: 'easeInOutQuad',
    autoplay: false
});

const rotateAnimation = anime.animate('#wp-icon', {
    rotate: '1turn',
    duration: 1000,
    easing: 'easeInOutQuad',
    autoplay: false
});

function playShrink() {
    shrinkAnimation.restart();
}

function playRotate() {
    rotateAnimation.restart();
}
