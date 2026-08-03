const enhancements = document.createElement('link');
enhancements.rel = 'stylesheet';
enhancements.href = 'enhancements.css';
document.head.appendChild(enhancements);

document.getElementById('year').textContent = new Date().getFullYear();
