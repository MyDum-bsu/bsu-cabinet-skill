function INIT_UR_ALL() {
    ReTableMainAll();
}


var list_dd = ['Curs', 'CountLine', 'CountGroups', 'CountGroupsSub', 'CountStudent', 'CountStudentAlien', 'Value', 'ValueResult',   'Description'];


function EDIT_UR_ALL() {
    $('#tbl-20').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) {
            CR_All = obj;
            $('.Type-all').text(CR_All.find('td').eq(0).text());

            $('#FacultyDop-all').val(CR_All.find('.Faculty').attr('vl'));
            ReFaculty(CR_All.find('.Spec').attr('vl'));
            $('#fo-all').val(CR_All.find('.FormEducation').attr('vl'));

            for (var i = 0; i < list_dd.length; i++) $('#' + list_dd[i] + '-all').val(CR_All.find('.' + list_dd[i]).text());

            $('.TableEdit-UP-ALL').dialog('open');
        }
    });


    $('#FacultyDop-all').change(function () { ReFaculty(0); });

    $('.TableEdit-UP-ALL').dialog({
        title: '', width: 600, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Сохранить', click: function () {

                          var ss = {
                              Key: CR_All.attr('vl'),
                              Faculty: $('#FacultyDop-all').val(),
                              FacultyText: $('#FacultyDop-all :selected').html(),
                              FormEducation: $('#fo-all').val(),
                              Spec: $('#SpecDop-all').val(),
                              SpecText: $('#SpecDop-all :selected').html()
                          };

                          CR_All.find('.Faculty').attr('vl', ss.Faculty).text(ss.FacultyText);
                          CR_All.find('.Spec').attr('vl', ss.Spec).text(ss.SpecText);
                          CR_All.find('.FormEducation').attr('vl', ss.FormEducation).text($('#fo-all :selected').html());

                          for (var i = 0; i < list_dd.length; i++) {
                              ss[list_dd[i]] = $('#' + list_dd[i] + '-all').val();
                              if (list_dd[i] == 'Value') ss['Value'] = int(ss['Value']);
                              CR_All.find('.' + list_dd[i]).text($('#' + list_dd[i] + '-all').val());
                          }
                          a(ss)
                          SendModel('Teacher/PersonalPlanMainSaveAll', ss, function (res) { ReTableMainAll(); });

                          $(this).dialog('close');
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });
}



function EDIT_UR_ALL_SMALL() {
    $('#tbl-20').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) {
            CR_All = obj;
            $('.Type-all').text(CR_All.find('td').eq(0).text());
            $('#Value-all').val(CR_All.find('.Value').text());
            $('.TableEdit-UP-ALL').dialog('open');
        }
    });

    $('.TableEdit-UP-ALL').dialog({
        title: '', width: 600, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
            {
                text: 'Сохранить', click: function () {

                    var ss = {
                        Key: CR_All.attr('vl'),
                        Value: $('#Value-all').val()
                    };
                    //a(ss)
                    SendModel('Teacher/PersonalPlanMainSaveAll_Plan', ss, function (res) {
                        CR_All.find('.Value').text(ss.Value);
                        ReTableMainAll();
                    });
                    $(this).dialog('close');
                }
            },
            { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });
}





function RESULT_UR_ALL() {
    $('.TableResult-UP-ALL').dialog({
        title: '', width: 600, dialogClass: 'dialog-work', autoOpen: false, modal: true,
        buttons: [
                  {
                      text: 'Сохранить', click: function () {
                          var ss = {
                              Key: CR_All.attr('vl'),
                              ValueResult: int($('#Result-all').val())
                          };
                          CR_All.find('.ValueResult').text(ss.ValueResult);
                          SendModel('Teacher/PersonalPlanMainSaveAllResult', ss, function (res) { ReTableMainAll(); });
                          $(this).dialog('close');
                      }
                  },
              { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('#tbl-20 .zone-result').click(function () {
        CR_All = $(this).closest('tr');
        $('.Type-all').text(CR_All.find('td').eq(0).text());
        $('#Result-all').val($(this).text());
        $('.TableResult-UP-ALL').dialog('open');
    });
}





function ReFaculty(vl) {
    var ss = { Faculty: $('#FacultyDop-all').val() };
    if (ss.Faculty == '0') {
        $('#SpecDop-all').html('<option value="0"></option>');
        return;
    }
    SendModel('Teacher/PersonalPlanSelectFaculty', ss, function (res) {
        $('#SpecDop-all').html('<option value="0"></option>' + res.Title);
        $('#SpecDop-all').val(vl);
    });
}

function ReTableMainAll() {
    //по строкам
    $('#tbl-20 tbody tr[vl]').each(function () {
        var obj = $(this).find('.Value');
        var vv = int(obj.text());
        $(this).find('.sum-plan').text(vv);
        $('#tbl-20 .itog-a td[pos="' + obj.attr('pos') + '"]').text(vv);
    });

    var sum = 0;
    $('#tbl-20 .itog-a td[pos]').each(function () {
        sum += int($(this).text());
    });
    $('#tbl-20 .itog-a .sum-itog').text(sum);

    sum = 0;
    $('#tbl-20 tbody tr[vl] .ValueResult').each(function () {
        var obj = $(this);
        var vv = int(obj.text());
        sum += vv;
        $('#tbl-20 .itog-b td[pos="' + obj.attr('pos') + '"]').text(vv);
    });
    $('#tbl-20 .itog-b .sum-itog').text(sum);
}


