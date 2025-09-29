/*
** alert 함수 **
state = 상태 icon = "warning" "error" "success" "info"
비동기 처리 위해 funType, url 받아와 사용
*/
function alertStart(state, title, message, funType, text, time) {
  return Swal.fire({
    icon: state,
    title: title,
    text: message,
  }).then(function () {
    if (funType == 'replace') {
      change_url(text, time)
    } else if (funType == 'click') {
      $(text).click()
    } else if (funType == 'reload') {
      location.reload()
    } else if (funType == 'focus') {
      $(text).focus()
    }
  });
};


/* 줄바꿈 용 Html 삽입 */
function alertStartHtml(state, title, message, target, funType, text, time) {
  return Swal.fire({
    icon: state,
    title: title,
    html: message,
    target: target
  }).then(function () {
    if (funType == 'replace') {
      change_url(text, time)
    } else if (funType == 'click') {
      $(text).click()
    } else if (funType == 'reload') {
      location.reload()
    } else if (funType == 'focus') {
      $(text).focus()
    }
  });
}
