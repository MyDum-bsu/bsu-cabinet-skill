function INIT_UR() {
    ReTableMain();
}

function EDIT_UR() {
    $('.toolbar-menu').ToolBarMdi([
{ title: 'Добавить пункт', cl: 'plus-outline vs vst-tbl-0 vst-tbl-10', func: function () { $('#Semestr').val(vl.Table == 0 ? 0 : 1);  $('.MainTableAdd').dialog('open'); } }
    ]);



    $('.MainTableAdd').dialog({
        title: '', width: 600,  dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Добавить', click: function () {
                          var ss = { Faculty: $('#Faculty').val(), Disp: $('#Disp').val(), Spec: $('#Spec').val(), Semestr: $('#Semestr').val(), FacultyText: $('#Faculty :selected').html(), DispText: $('#Disp :selected').html(), SpecText: $('#Spec :selected').html() };
                          SendModel('Teacher/PersonalPlanMainAdd', ss, function (res) {
                              var jj = $('<tr key=' + res.obj.Key + '><td>' + res.obj.DispText + '</td><td>' + res.obj.FacultyText + '</td><td></td><td></td><td>' + res.obj.SpecText + '</td>' + RepeatTd(27) + '</tr>');
                              $('#tbl-' + vl.Table + ' tbody').prepend(jj);
                              $('#tbl-'+vl.Table).ContentToolbar('re');
                          });
                          $(this).dialog('close'); 
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('.MainTableEdit').dialog({
        title: '', width: 600,  dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Сохранить', click: function () {
                          var ss = {
                              Key: CR_All.attr('key'),

                              Curs: $('#Curs').val(),
                              FormEducation: $('#fo').val(),
                          };

                          CR_All.find('.Curs').text(ss.Curs);
                          CR_All.find('.FormEducation').attr('vl', ss.FormEducation).text($('#fo :selected').html());

                          for (var i = 0; i < list_ss.length; i++) {
                              ss[list_ss[i]] = int($('#' + list_ss[i]).val());
                              CR_All.find('.' + list_ss[i]).text($('#' + list_ss[i]).val());
                          }
                          //console.log(ss);
                          ss['Description'] = $('#Description').val();

                          SendModel('Teacher/PersonalPlanMainSave', ss, function (res) { ReTableMain(); });

                          $(this).dialog('close');
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });
  
    $('#Faculty').change(function () {
        var ss = { Faculty: $('#Faculty').val() };
        SendModel('Teacher/PersonalPlanSelectFaculty', ss, function (res) {
            $('#Spec').html(res.Title);
            $('#Disp').html(res.obj);

        });
    });
    $('#Faculty').trigger('change');
 
    $('#tbl-0').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) { MaintTableEdit(obj); },
        del: function (vlb, obj) { TblDel(obj); }
    });

    $('#tbl-10').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) {MaintTableEdit(obj);},
        del: function (vlb, obj) { TblDel(obj); }
    });

}

var EditCell;

function RESULT_UR() {
    $('.MainTableEdit-result').dialog({
        title: '', width: 300, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Сохранить', click: function () {
                          var ss = {
                              Key: null,
                              Semestr:0,
                              Result: int($('#Result').val())
                          };
                          EditCell.text(ss.Result);
                          if (CR_All.is("[key]")) {
                              ss.Key = CR_All.attr('key');
                              SendModel('Teacher/PersonalPlanMainSaveResult_A', ss, function (res) { ReTableMain() });
                          } else {
                              ss.Key =EditCell.attr('kv');
                              ss.Semestr = vl.Table == 10 ? 1 : 0;
                              SendModel('Teacher/PersonalPlanMainSaveResult_B', ss, function (res) { ReTableMain() });
                          }
                          $(this).dialog('close');
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });



    $('#tbl-0 .zone-result, #tbl-10 .zone-result ').click(function () {
        EditCell = $(this);
        CR_All = $(this).closest('tr');
        $('#Result').val($(this).text());
        $('.MainTableEdit-result').dialog('open');
    });

}







var list_ss = ['CountLine', 'CountGroups', 'CountGroupsSub',
    'CountStudent_lecture','CountStudent_practical','CountStudent_laboratory','CountStudentAlien',
    'Clock_lecture_a', 'Clock_lecture_b', 'Clock_practical_a', 'Clock_practical_b', 'Clock_laboratory_a', 'Clock_laboratory_b', 'Clock_ucr_a', 'Clock_ucr_b',
    'Collocvium',
    'Help_now', 'Help_personal', 'Help_group',
    'Check_control',
    'Exam', 'Credit',
    'Description'];

function MaintTableEdit(obj) {
    CR_All = obj;

    $('#fo').val(obj.find('.FormEducation').attr('vl'));
    $('#Curs').val(obj.find('.Curs').text());

    for (var i = 0; i < list_ss.length; i++) $('#' + list_ss[i]).val(obj.find('.' + list_ss[i]).text());

    $('.MainTableEdit').dialog('open');
}


function roundToTwo(num) {
    return +(Math.round(num + "e+2") + "e-2");
}

function ReTableMain() {
     //по строкам
    $('#tbl-0 tbody tr[key], #tbl-10 tbody tr[key]').each(function () {
            var sum = 0;
            $(this).find('td.to-sum').each(function () { sum += int($(this).text()); });
            $(this).find('.sum').text(sum.toFixed(2));
            
        });

    //по столбцам
    $('.res-a').each(function () {
        $(this).find('.sum-a').each(function () {
            var sum = 0;
            $(this).closest('table').find('.'+$(this).attr('kv')).each(function () {
                sum += int($(this).text());
            });
            $(this).text(roundToTwo(sum));
        });
    });

    //итого по плану (осень)
    var sum = 0;
    $('#tbl-0 .res-a .sum-a').each(function () { sum += int($(this).text()); });
    $('#tbl-0 .res-a .sum-itog').text(sum.toFixed(2));

    //итого по плану (весна)
    var sum = 0;
    $('#tbl-10 .res-a .sum-a').each(function () { sum += int($(this).text()); });
    $('#tbl-10 .res-a .sum-itog').text(sum.toFixed(2));


    //итого по отчёту (осень)
    var sum_a = 0;
    $('#tbl-0 .res-b .zone-result, #tbl-0 .res-b .auto-sum').each(function () { sum_a += int($(this).text()); });
    var sum_b = 0;
    $('#tbl-0 tr[key] .zone-result').each(function () { sum_b += int($(this).text()); });
    $('#tbl-0 .res-b .sum-itog').text(sum_a.toFixed(2)+'/'+sum_b.toFixed(2));
    $('#tbl-0 .res-b .sum-itog').css('background-color', sum_a==sum_b?'white':'red')


    //итого по отчёту (весна)
    var sum_a = 0;
    $('#tbl-10 .res-b .zone-result, #tbl-10 .res-b .auto-sum').each(function () { sum_a += int($(this).text()); });
    var sum_b = 0;
    $('#tbl-10 tr[key] .zone-result').each(function () { sum_b += int($(this).text()); });
    $('#tbl-10 .res-b .sum-itog').text(sum_a.toFixed(2) + '/' + sum_b.toFixed(2));
    $('#tbl-10 .res-b .sum-itog').css('background-color', sum_a == sum_b ? 'white' : 'red');
}

