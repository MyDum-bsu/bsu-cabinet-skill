var SelObj = null;
var SelKey = null;

$(function () {

    if ($('#IdEmpStaff option').length > 1) {
        $('#IdEmpStaff').show().change(function () {
            Ajax_Show();
            window.location = '/Teacher/CardOne/' + $(this).val();
        });
        $('.CurrentRole').after($('#IdEmpStaff'));
    }
    else {
        $('.CurrentRole').after($('<span class="staff-one">' + $('#IdEmpStaff option').eq(0).text() + '</span>'));
    }



    switch ($('.main-tree div[vl]').length) {
        case 0:
            $('.main-tree').html('');
            $('.main-zone').html('<div class="hd">Перед заполнением карточки Вам небходимо заполнить индивидуальный план.</div>');
            return;
        default:
            $('.toolbar-menu').ToolBarMdi([
                {
                    title: 'Добавить', cl: 'plus-outline vs', func: function () {
                        SelObj = null;
                        CardEdit(0);
                    }
                },
                {
                    title: 'Загрузить', cl: 'file-pdf', func: function () {
                        var ss = { Key: SelKey, Status: 0 };
                        SendModel('Teacher/CardOnePdf', ss, function (res) { });
                    }
                },
                {
                    title: 'Дублировать', cl: 'content-copy vs', func: function () {
                        $('.CardCopy-dialog').dialog('open');
                    }
                }
            ]);
            break;
    }

   
    var tt = window.location.hash.substring(1);

    $('.main-tree').TabsList(function (vl, obj) {
        SelKey = vl;
        $('.table-card tbody tr, .sp-block').hide();
        $('.table-card tbody tr[vl="' + vl + '"],.sp-block[vl="' + vl + '"]').show();
        if(vl!="ALLYEAR") $('.sp-block[vl="NOTALL"]').show();
        window.location = '/Teacher/CardOne/' + $('#IdEmpStaff').val()+'#'+SelKey;
    });
    $('.main-tree [vl="' + tt + '"]').trigger('click');;


    $('#l_dt').datepicker({ dateFormat: "dd-mm-yy" });

    $('#l_status').change(function () {
        $('.sf').hide();
        $('.tr_time').show();
        switch (parseInt($(this).val())) {
            case 0:
                $('.tr_topic, .tr_dl, .tr_hour').show();
                break;
            case 1:
            case 2:
            case 4:
            case 5:
            case 8:
                $('.tr_topic, .tr_dl, .tr_group, .tr_hour').show();
                break;
            case 3:
                $('.tr_topic,.tr_statusHelp, .tr_group, .tr_hour').show();
                break;
            case 6:
                $('.tr_statusCheck, .tr_group, .tr_check, .tr_groupCount, .tr_normTime').show();
                $('.tr_time').hide();
                break;
            case 7:
                $('.tr_statusExamA, .tr_statusExamB, .tr_group, .tr_groupCount, .tr_student').show();
                $('.tr_time').hide();
                break;

        }

    });

    $('#l_ready').click(function () { $(this).toggleClass('checkbox-on'); })
    $('#l_dl').click(function () { $(this).toggleClass('checkbox-on'); })

    for (var i = 0; i < 9; i++)  ///<------------------------------------------------------------ Сюда
    {
        $('#tbl-'+i).data('status',i).ContentToolbar({
            list: 'td:first-child', text: 'Запись',
            edit: function (vlb, obj) { SelObj = obj; CardEdit(obj.closest('table').data('status')); },
            del: function (vlb, obj) { var ss = obj.closest('table').data('status'); obj.remove(); CardSave(ss); }
        });
    }
    $('.SendForm').CheckStart();

    
    //$('.copy-from').html($('.main-tree').html());
    $('.copy-list').TabsList(function () { });
    
    $('.CardCopy-dialog').dialog({
        title: 'Дублирование', width: 600, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Лекции', click: function () { CardCopy(0);}
                 },
                  {
                      text: 'Лаб.', click: function () {CardCopy(1);}
                  },
                  {
                      text: 'УСР.', click: function () {CardCopy(2);}
                  }
                  ,
                  {
                      text: 'Конс.', click: function () {CardCopy(3);}
                  }
                  ,
                  {
                      text: 'Прак.Сем.', click: function () {CardCopy(4);}
                  }
                  ,

              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });


    $('.Remark-dialog').dialog({
        title: '', width: 600, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Применить', click: function () {
                          $(this).dialog('close'); 
                          $('#remark-' + SelKey).text($('#l_remark').val());
                          var ss = { Key: SelKey, Remark: $('#l_remark').val()};
                          SendModel('Teacher/CardOneSaveRemark', ss, function (res) { });
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('.remark').click(function () {
        $('.Remark-dialog').dialog('open');
        $('#l_remark').val($('#remark-' + SelKey).text());
    })


    EDIT_UR_ALL();
 });

/****************************************************************/
/*                        Copy                            */
/****************************************************************/
function CardCopy(status) {

    var from = $('.copy-list').find('.tabs-on');
    var vl = { idYear: from.attr('yy'), IdDept: from.attr('dp'), Key: from.attr('vl'), Status: status}
//    console.log(vl)
    SendModel('Teacher/CardOneForCopy', vl, function (res) {
        res = res.obj;
        if (res.length > 0) {
            for (var k = 0; k < res.length; k++) {
                var obj = $(res[k]);
                obj.attr('vl', SelKey);
                $('#tbl-' + status + ' tbody').append(obj);
            }
            $('#tbl-' + status).ContentToolbar('re');
            CardSave(status);
        }
    });

    $('.CardCopy-dialog').dialog('close');
}

/****************************************************************/
/*                        Save                            */
/****************************************************************/
function CardSave(status) {
    var ss = { Key: SelKey, Status: status, Items: Array()};
    $('#tbl-' + status + ' tbody tr[vl="' + SelKey + '"]').each(function () {
        var obj = $(this);
        var tt = {
            title: obj.find('td').eq(0).text(),//+'[|]'+obj.find('.a-note').text(),
            group: obj.find('.a-group').text(),
            groupCount: obj.find('.a-groupCount').text(),
            dt: obj.find('.a-dt').text(),
            time: obj.find('.a-time').text(),
            hours: int( obj.find('.a-hours').text()),
            ready: obj.find('.a-ready .checkbox-on').length > 0 ? true : false,
            dl: obj.find('.a-dl .checkbox-on').length > 0 ? true : false,
            statusHelp: obj.find('.a-statusHelp').attr('vl'),
            statusCheck: obj.find('.a-statusCheck').attr('vl'),
            check: obj.find('.a-check').text(),

            statusExamA: obj.find('.a-statusExamA').attr('vl'),
            statusExamB: obj.find('.a-statusExamB').attr('vl'),
            student: obj.find('.a-student').text(),
            normTime: Decimal(obj.find('.a-normTime').text()),


            note: obj.find('.a-note').text()
        };
        ss.Items.push(tt);
    });

    SendModel('Teacher/CardOneSave', ss, function (res) {
        if (IsAdd) window.location.reload();
    });
}

function ListGroups(str) {
    var arr = new Array();
    var num = 0;
    for (var k = 0; k < str.length; k++) {
        var c = str[k];
        if (c == ';') {
            if (num > 0 && num < 31) arr.push(num);
            num = 0;
            continue;
        }
        if (c == '-') {
            var numEnd = 0;
            k++;
            for (; k < str.length; k++) {
                var c = str[k];
                if (c >= '0' && c <= '9') numEnd = numEnd * 10 + parseInt(c); else break;
            }
            k--;
            if (num > 0 && num < 31 && numEnd > 0 && numEnd < 31 && num < numEnd) {
                for (var n = num; n <=numEnd;n++) arr.push(n);
            }
            num = 0;
            continue;
        }

        if (c >= '0' && c <= '9') {
            num = num * 10 + parseInt(c);
            continue;
        }

    }
    if (num > 0 && num < 31) arr.push(num);

    if (arr.length == 0) {
        arr.push('');
        return arr;
    }

    return arr.sort(function (a, b) {if (a<b) return -1; if (a>b) return 1; return 0;});
}

/****************************************************************/
/*                        Edit                            */
/****************************************************************/

var IsAdd = false;

function CardEdit(status) {
    $('#l_title, #l_hour, #l_dt, #l_timeб #l_note').val('');
    $('#l_ready').removeClass('checkbox-on')
    $('#l_dl').removeClass('checkbox-on')
    $('#l_status').removeAttr('disabled');

    if (SelObj != null) {
        $('#l_title').val(SelObj.find('td').eq(0).text());
        $('#l_hour').val(SelObj.find('.a-hours').text());
        $('#l_dt').val(SelObj.find('.a-dt').text());
        $('#l_time').val(SelObj.find('.a-time').text());
        $('#l_status').val(status).attr('disabled', 'disabled');
        $('#l_note').val(SelObj.find('.a-note').text());
        if (status != 0) $('#l_group').val(SelObj.find('.a-group').text());
        $('#l_groupCount').val(SelObj.find('.a-groupCount').text());
        if (SelObj.find('.a-ready .checkbox-on').length > 0) $('#l_ready').addClass('checkbox-on')
        if (SelObj.find('.a-dl .checkbox-on').length > 0) $('#l_dl').addClass('checkbox-on')

        try { $('#l_statusHelp').val(SelObj.find('.a-statusHelp').attr('vl')); } catch (e) { }
        try { $('#l_statusCheck').val(SelObj.find('.a-statusCheck').attr('vl')); } catch (e) { }

        try { $('#l_statusExamA').val(SelObj.find('.a-statusExamA').attr('vl')); } catch (e) { }
        try { $('#l_statusExamB').val(SelObj.find('.a-statusExamB').attr('vl')); } catch (e) { }
        $('#l_student').val(SelObj.find('.a-student').text());
        $('#l_normTime').val(SelObj.find('.a-normTime').text());
    }
    $('#l_status').trigger('change');

    $('.CardEdit-dialog').dialog({
        title: '', width: 400, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Сохранить', click: function () {
                          if ($('.SendForm').Check() == false) return;
                          if (SelObj == null) {
                              IsAdd = true;
                              status = $('#l_status').val();
                              if (status == 0) $('#l_group').val('');
                              var ar = ListGroups($('#l_group').val());
                              for (var k = 0; k < ar.length; k++) {
                                  SelObj = $('<tr><td></td><td class="a-dl"><span class="checkbox-off"></span></td><td class="a-statusHelp"></td><td class="a-group"></td><td class="a-dt"></td>'
                                      +'<td class="a-time"></td><td class="a-hours"></td><td class="a-ready"><span class="checkbox-off"></span></td><td class="a-note"></td>'
                                      + '<td class="a-statusCheck"></td><td class="a-check"></td><td class="a-normTime"></td><td class="a-groupCount"></td>'
                                      + '<td class="a-statusExamA"></td><td class="a-statusExamB"></td><td class="a-student"></td>'
                                      + '</tr>');

                                  SelObj.attr('vl', SelKey);
                                  if (status == 0) SelObj.find('.a-group').remove();
                                  if (status == 3) {
                                      SelObj.find('.a-dl').remove();
                                  }
                                  else {
                                      SelObj.find('.a-statusHelp').remove();
                                  }
                                  $('#tbl-' + status + ' tbody').append(SelObj);
                                  $('#tbl-' + status).ContentToolbar('re');
                                  CardEditOne(status, ar[k])
                              }
                          }
                          else {
                              IsAdd = false;
                              CardEditOne(status, $('#l_group').val())
                          }

                          $('.CardEdit-dialog').dialog('close');
                          CardSave(status);
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });
    $('.CardEdit-dialog').dialog('open');
}

function CardEditOne(status, group) {
    //console.log(SelObj)
    SelObj.find('td').eq(0).find('span.text').text($('#l_title').val());
    SelObj.find('.a-hours').text($('#l_hour').val());
    SelObj.find('.a-dt').text($('#l_dt').val());
    SelObj.find('.a-time').text($('#l_time').val());
    SelObj.find('.a-note').text($('#l_note').val());
    SelObj.find('.a-ready .checkbox-off').removeClass('checkbox-on');
    if ($('#l_ready').hasClass('checkbox-on')) SelObj.find('.a-ready .checkbox-off').addClass('checkbox-on')
    SelObj.find('.a-dl .checkbox-off').removeClass('checkbox-on');
    if ($('#l_dl').hasClass('checkbox-on')) SelObj.find('.a-dl .checkbox-off').addClass('checkbox-on')

    if (status != 0) {
        SelObj.find('.a-group').text(group);
    }
    if (status == 3) {
        SelObj.find('.a-statusHelp').attr('vl', $('#l_statusHelp').val());
        SelObj.find('.a-statusHelp').text($('#l_statusHelp :selected').text());
    }
    if (status == 6) {
        SelObj.find('.a-statusCheck').attr('vl', $('#l_statusCheck').val());
        SelObj.find('.a-statusCheck').text($('#l_statusCheck :selected').text());
        SelObj.find('.a-check').text($('#l_check').val());
        SelObj.find('.a-normTime').text($('#l_normTime').val());
        SelObj.find('.a-groupCount').text($('#l_groupCount').val());
    }
    if (status == 7) {
        SelObj.find('.a-statusExamA').attr('vl', $('#l_statusExamA').val());
        SelObj.find('.a-statusExamA').text($('#l_statusExamA :selected').text());
        SelObj.find('.a-statusExamB').attr('vl', $('#l_statusExamB').val());
        SelObj.find('.a-statusExamB').text($('#l_statusExamB :selected').text());
        SelObj.find('.a-student').text($('#l_student').val());
        SelObj.find('.a-groupCount').text($('#l_groupCount').val());
    }

}

function int(vl) {
    var tt = parseFloat(vl.replace(',', '.'));
    if (isNaN(tt)) tt = parseFloat(vl.replace('.', ','));
    return (isNaN(tt) == false) ? tt : 0;
}