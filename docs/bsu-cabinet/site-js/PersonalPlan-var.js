function INIT_VAR() {

}

function EDIT_VAR() {
    $('.toolbar-menu').ToolBarMdi([
    { title: 'Добавить пункт', cl: 'plus-outline vs vst-tbl-2', func: function () { TblAdd23('#tbl-2'); } },
    { title: 'Добавить пункт', cl: 'plus-outline vs vst-tbl-3', func: function () { TblAdd23('#tbl-3'); } },
    { title: 'Добавить пункт', cl: 'plus-outline vs vst-tbl-4', func: function () { TblAdd45('#tbl-4'); } },
    { title: 'Добавить пункт', cl: 'plus-outline vs vst-tbl-5', func: function () { TblAdd45('#tbl-5'); } }
    ]);

    $('#tbl-2').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) { TblEdit23(obj); },
        del: function (vlb, obj) { TblDel(obj); }
    });

    $('#tbl-3').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) { TblEdit23(obj); },
        del: function (vlb, obj) { TblDel(obj); }
    });

    $('#tbl-4').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) { TblEdit45(obj); },
        del: function (vlb, obj) { TblDel(obj); }
    });


    $('#tbl-5').ContentToolbar({
        list: 'td:first-child', text: 'Запись',
        edit: function (vlb, obj) { TblEdit45(obj); },
        del: function (vlb, obj) { TblDel(obj); }
    });


    $('.edit-table45-dialog').dialog({ title: '', width: 800, dialogClass: 'dialog-work', autoOpen: false, modal: true });
    $('.edit-table23-dialog').dialog({ title: '', width: 800, dialogClass: 'dialog-work', autoOpen: false, modal: true });
    $('#dtstart, #dtend').datepicker({ dateFormat: "dd-mm-yy" });
}

function RESULT_VAR() {

}




/*********************************************************************************************/
/*                  Table 2-3                                                               */
/*********************************************************************************************/

function TblAdd23(name) {
    $('#df23, #dtstart, #dtend').val('');
    vl.Oper = 'add';

    $('.edit-table23-dialog').dialog({
        title: 'Добавление записи',
        buttons: [
                {
                    text: 'Добавить', click: function () {
                        vl.Text = $('#df23').val();
                        if (vl.Text == '') return;
                        vl.DtStart = $('#dtstart').val();
                        vl.DtEnd = $('#dtend').val();

                        //console.log(vl);

                        SendModel('Teacher/PersonalPlanSaveTable', vl, function (res) {
                            var jj = $('<tr key=' + res.obj.Key + '><td>' + res.obj.Text + '</td><td>' + res.obj.DtStart + '</td><td>' + res.obj.DtEnd + '</td>' + RepeatTd(3) + '</tr>');
                            $(name + ' tbody').append(jj);
                            $(name).ContentToolbar('re');
                        });
                        $(this).dialog('close');
                    }
                },
            { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('.edit-table23-dialog').dialog('open');
}

function TblEdit23(obj) {
    vl.Key = obj.attr('key');
    vl.Oper = 'edit';
    $('#df23').val(obj.find('td').eq(0).text());
    $('#dtstart').val(obj.find('td').eq(1).text());
    $('#dtend').val(obj.find('td').eq(2).text());


    $('.edit-table23-dialog').dialog({
        title: 'Изменение данных',
        buttons: [
      {
          text: 'Сохранить', click: function () {
              vl.Text = $('#df23').val();
              if (vl.Text == '') return;
              vl.DtStart = $('#dtstart').val();
              vl.DtEnd = $('#dtend').val();
              SendModel('Teacher/PersonalPlanSaveTable', vl, function (res) {
                  obj.find('.tool .text').text(vl.Text);
                  obj.find('td').eq(1).text(vl.DtStart);
                  obj.find('td').eq(2).text(vl.DtEnd);
              });
              $(this).dialog('close');
          }
      },
  { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('.edit-table23-dialog').dialog('open');
}



/*********************************************************************************************/
/*                  Table 4-5                                                               */
/*********************************************************************************************/
function TblAdd45(name) {
    $('#df45').val('');
    vl.Oper = 'add';

    $('.edit-table45-dialog').dialog({
        title: 'Добавление записи',
        buttons: [
                {
                    text: 'Добавить', click: function () {
                        vl.Text = $('#df45').val();
                        if (vl.Text == '') return;
                        SendModel('Teacher/PersonalPlanSaveTable', vl, function (res) {
                            var jj = $('<tr key=' + res.obj.Key + '><td>' + res.obj.Text + '</td><td></td></tr>');
                            $(name + ' tbody').append(jj);
                            $(name).ContentToolbar('re');
                        });
                        $(this).dialog('close');
                    }
                },
            { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('.edit-table45-dialog').dialog('open');
}


function TblEdit45(obj) {
    vl.Key = obj.attr('key');
    vl.Oper = 'edit';
    $('#df45').val(obj.find('td:first-child').text());

    $('.edit-table45-dialog').dialog({
        title: 'Изменение данных',
        buttons: [
      {
          text: 'Сохранить', click: function () {
              vl.Text = $('#df45').val();
              if (vl.Text == '') return;
              SendModel('Teacher/PersonalPlanSaveTable', vl, function (res) { obj.find('.tool .text').text(vl.Text); });
              $(this).dialog('close');
          }
      },
  { text: 'Закрыть', click: function () { $(this).dialog('close'); } }
        ]
    });

    $('.edit-table45-dialog').dialog('open');
}